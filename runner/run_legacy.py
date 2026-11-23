"""Run a CardDemo JCL job locally with GnuCOBOL and capture golden output.

    python -m runner.run_legacy READACCT            # 2 runs, golden/READACCT/
    python runner/run_legacy.py POSTTRAN --runs 3
    python -m runner.run_legacy READACCT --clock 2026-01-15T12:00:00Z

For each run, in a fresh workspace (build/work/<JOB>/runN/):
  1. Build the step programs, stubs and clock shim (runner/build.py).
  2. Check that the frozen clock is really in effect (CLOCKCHK).
  3. Execute each EXEC step: allocate DDs (DISP status), bind each DD name to
     a local file through DD_<ddname> env vars (GnuCOBOL's ASSIGN EXTERNAL
     lookup), run the program, then apply the normal or abnormal disposition.
     Input datasets are materialised on first use: PS from the seed text,
     KSDS by loading the seed the cluster is REPRO'd from in CardDemo's JCL.
  4. Collect results: SYSOUT per step, every dataset the job created or
     changed (KSDS unloaded to FB in key order), and manifest.json.

Golden output is written to golden/<JOB>/ only if every run is byte-identical.
"""
from __future__ import annotations

import argparse
import datetime as dt
import filecmp
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

if __package__ in (None, ""):                       # python runner/run_legacy.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "runner"

from . import build                                  # noqa: E402
from .catalog import (JCL_DIR, REPO, Catalog, build_catalog,  # noqa: E402
                      find_job, load_seed)
from .jcl import DD, Job, Step, UnsupportedJCL, parse_jcl  # noqa: E402

LEGACY_COMMIT = "59cc6c2fd7ebd7ef7925cad552a01a4b8b6e4d5e"   # docs/legacy-setup.md W1
DEFAULT_CLOCK = "2026-01-15T12:00:00Z"
GOLDEN = REPO / "golden"
WORK = build.BUILD / "work"
NO_OP_PGMS = {"IEFBR14"}
IGNORED_DDS = {"STEPLIB", "JOBLIB"}       # load libraries: programs come from build/bin


class RunError(Exception):
    pass


class NeedsUpstream(RunError):
    """An input dataset has no seed and no earlier step in this job created it."""


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@dataclass
class Dataset:
    dsn: str
    path: Path
    org: str             # PS | KSDS
    origin: str          # seed | job
    lrecl: int | None
    recfm: str | None
    seed_image: bytes | None = None   # FB image the dataset started from
    seed_file: str | None = None


class JobRun:
    def __init__(self, job: Job, cat: Catalog, workdir: Path, epoch: int):
        self.job, self.cat, self.work, self.epoch = job, cat, workdir, epoch
        self.dsdir = workdir / "datasets"
        self.sysout = workdir / "sysout"
        self.datasets: dict[str, Dataset] = {}
        self.gdg_current: dict[str, int] = {}
        self.gdg_start: dict[str, int] = {}
        self.steps: list[dict] = []
        self.loads: list[dict] = []
        for d in (self.dsdir, self.sysout):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ env --
    @staticmethod
    def base_env() -> dict[str, str]:
        return {
            "PATH": "/usr/bin:/bin",
            "LANG": "C",
            "LC_ALL": "C",
            "TZ": "UTC",
            "COB_LIBRARY_PATH": str(build.LIB),
            "COB_VARSEQ_FORMAT": "0",
        }

    def program_env(self) -> dict[str, str]:
        env = self.base_env()
        env["LD_PRELOAD"] = str(build.LIB / "libfrozenclock.so")
        env["LEGACY_FROZEN_EPOCH"] = str(self.epoch)
        return env

    # ------------------------------------------------------- datasets --
    def resolve(self, raw: str) -> tuple[str, str | None]:
        """DSN as coded -> (actual DSN, GDG base or None)."""
        m = re.match(r"^(.+)\(([+-]?\d+)\)$", raw)
        if not m:
            if "(" in raw:
                raise UnsupportedJCL(f"PDS member reference not supported: {raw}")
            return raw, None
        base, rel = m.group(1), int(m.group(2))
        if base not in self.cat.gdg_bases:
            raise UnsupportedJCL(f"{raw}: {base} is not a GDG base in the catalog")
        # Relative generations are fixed at job start (z/OS semantics).
        start = self.gdg_start.setdefault(base, self.gdg_current.get(base, 0))
        gen = start + rel
        if gen < 1:
            raise NeedsUpstream(f"{raw}: generation {gen} does not exist")
        return f"{base}.G{gen:04d}V00", base

    def path_for(self, dsn: str) -> Path:
        return self.dsdir / dsn

    def materialise(self, dsn: str, where: str) -> Dataset:
        if dsn in self.datasets:
            return self.datasets[dsn]
        cl = self.cat.clusters.get(dsn)
        if cl is not None:
            if cl.org != "KSDS":
                raise UnsupportedJCL(f"{dsn}: VSAM {cl.org} not supported")
            if dsn in self.cat.repro_conflicts:
                raise RunError(f"{where}: JCL files disagree on what loads {dsn}: "
                               f"{sorted(self.cat.repro_conflicts[dsn])}")
            src = self.cat.repro.get(dsn)
            seed = self.cat.seed_for(src[0]) if src else None
            if seed is None:
                raise NeedsUpstream(
                    f"{where}: KSDS {dsn} is loaded from {src[0] if src else '(nothing)'} "
                    f"in {cl.defined_in}, which has no seed in legacy/app/data")
            seed_path, lrecl, mode = seed
            if lrecl != cl.rec_max or cl.rec_avg != cl.rec_max:
                raise UnsupportedJCL(f"{dsn}: variable RECORDSIZE({cl.rec_avg} {cl.rec_max})")
            image, stats = load_seed(seed_path, lrecl, mode)
            ps = self.work / "seed" / src[0]
            ps.parent.mkdir(exist_ok=True)
            ps.write_bytes(image)
            exe = build.ksds_utility("load", cl.rec_max, cl.keyoff, cl.keylen)
            out = subprocess.run([str(exe)], cwd=self.work, capture_output=True,
                                 env={**self.base_env(), "DD_SEQFILE": str(ps),
                                      "DD_KSDSFILE": str(self.path_for(dsn))})
            if out.returncode != 0:
                raise RunError(f"loading {dsn} from {seed_path.name} failed:\n"
                               + out.stdout.decode("latin-1") + out.stderr.decode("latin-1"))
            self.loads.append({"dsn": dsn, "from_dsn": src[0], "seed": seed_path.name,
                               "via": f"REPRO in {src[1]}", "seed_normalisation": stats,
                               "loader": out.stdout.decode("latin-1").strip()})
            ds = Dataset(dsn, self.path_for(dsn), "KSDS", "seed", lrecl, "F",
                         seed_image=image, seed_file=seed_path.name)
        else:
            seed = self.cat.seed_for(dsn)
            if seed is None:
                raise NeedsUpstream(f"{where}: {dsn} has no seed and was not created earlier "
                                    "in this job (needs a golden run of the upstream job)")
            seed_path, lrecl, mode = seed
            image, stats = load_seed(seed_path, lrecl, mode)
            self.path_for(dsn).write_bytes(image)
            self.loads.append({"dsn": dsn, "seed": seed_path.name, "seed_normalisation": stats})
            ds = Dataset(dsn, self.path_for(dsn), "PS", "seed", lrecl, "FB",
                         seed_image=image, seed_file=seed_path.name)
        self.datasets[dsn] = ds
        return ds

    # ---------------------------------------------------------- steps --
    def run_step(self, step: Step) -> dict:
        if "COND" in step.params:
            raise UnsupportedJCL(f"{step.name}: COND= not supported")
        where = f"{self.job.name}.{step.name}"
        env = self.program_env()
        allocs = []                                  # (dd, dsn, gdg_base)
        dd_report = []
        for dd in step.dds:
            if dd.name in IGNORED_DDS:
                dd_report.append({"dd": dd.name, "ignored": "load library"})
                continue
            if dd.sysout:
                p = self.sysout / f"{step.name}.{dd.name}"
                p.touch()
                env[f"DD_{dd.name}"] = str(p)
                dd_report.append({"dd": dd.name, "sysout": dd.params["SYSOUT"]})
                continue
            if dd.dummy:
                env[f"DD_{dd.name}"] = os.devnull
                dd_report.append({"dd": dd.name, "dummy": True})
                continue
            if dd.instream is not None:
                p = self.work / "instream" / f"{step.name}.{dd.name}"
                p.parent.mkdir(exist_ok=True)
                p.write_bytes(b"".join(l.encode("latin-1").ljust(80)[:80] for l in dd.instream))
                env[f"DD_{dd.name}"] = str(p)
                dd_report.append({"dd": dd.name, "instream_records": len(dd.instream)})
                continue
            if dd.dsn is None:
                raise UnsupportedJCL(f"{where}: DD {dd.name} has no DSN")
            dsn, base = self.resolve(dd.dsn)
            status, normal, abnormal = dd.disp()
            dcb = dd.dcb()
            if status in ("SHR", "OLD"):
                ds = self.materialise(dsn, f"{where}.{dd.name}")
            elif status == "NEW":
                if dsn in self.datasets:
                    raise RunError(f"{where}.{dd.name}: {dsn} already exists (DISP=NEW)")
                lrecl = int(dcb["LRECL"]) if "LRECL" in dcb else None
                ds = Dataset(dsn, self.path_for(dsn), "PS", "job", lrecl, dcb.get("RECFM"))
                ds.path.write_bytes(b"")             # allocation creates it empty
                self.datasets[dsn] = ds
            elif status == "MOD":
                if step.pgm not in NO_OP_PGMS:
                    raise UnsupportedJCL(f"{where}.{dd.name}: DISP=MOD in a program step "
                                         "(append semantics) not supported")
                ds = self.datasets.get(dsn)
                if ds is None:
                    ds = Dataset(dsn, self.path_for(dsn), "PS", "job", None, None)
                    ds.path.write_bytes(b"")
                    self.datasets[dsn] = ds
            else:
                raise UnsupportedJCL(f"{where}.{dd.name}: DISP status {status}")
            env[f"DD_{dd.name}"] = str(ds.path)
            allocs.append((dd, dsn, base))
            dd_report.append({"dd": dd.name, "dsn": dsn, "coded_dsn": dd.dsn,
                              "disp": [status, normal, abnormal]})

        rec = {"step": step.name, "pgm": step.pgm, "dds": dd_report}
        abended = False
        if step.pgm in NO_OP_PGMS:
            rec["result"] = {"rc": 0}
        else:
            prog = self.programs[step.pgm]
            abend_file = self.work / f"abend.{step.name}"
            env["LEGACY_ABEND_FILE"] = str(abend_file)
            proc = subprocess.run([str(prog["output"])], cwd=self.work, env=env,
                                  capture_output=True, timeout=900)
            (self.sysout / f"{step.name}.DISPLAY").write_bytes(proc.stdout)
            (self.sysout / f"{step.name}.stderr").write_bytes(proc.stderr)
            if proc.returncode == build.ABEND_EXIT and abend_file.exists():
                abended = True
                rec["result"] = {"abend": abend_file.read_text().split()[0]}
            elif proc.returncode < 0:
                abended = True
                rec["result"] = {"abend": f"signal {-proc.returncode}"}
            elif proc.returncode != 0 and b"libcob: " in proc.stderr and b"error" in proc.stderr:
                abended = True
                rec["result"] = {"abend": "libcob runtime error", "rc": proc.returncode}
            else:
                rec["result"] = {"rc": proc.returncode}
            if b"cannot be preloaded" in proc.stderr:
                raise RunError(f"{where}: frozen clock shim was not loaded")

        # dispositions
        for dd, dsn, base in allocs:
            _, normal, abnormal = dd.disp()
            action = abnormal if abended else normal
            if action == "DELETE":
                ds = self.datasets.pop(dsn, None)
                if ds and ds.path.exists():
                    ds.path.unlink()
            elif action in ("CATLG", "KEEP", "PASS", "UNCATLG"):
                if base and action == "CATLG":
                    gen = int(dsn.rsplit(".G", 1)[1][:4])
                    self.gdg_current[base] = max(self.gdg_current.get(base, 0), gen)
            else:
                raise UnsupportedJCL(f"{where}.{dd.name}: disposition {action}")
        rec["abended"] = abended
        return rec

    def run(self, programs: dict[str, dict]) -> None:
        self.programs = programs
        for step in self.job.steps:
            rec = self.run_step(step)
            self.steps.append(rec)
            if rec["abended"]:
                for rest in self.job.steps[len(self.steps):]:
                    self.steps.append({"step": rest.name, "pgm": rest.pgm,
                                       "result": {"skipped": "earlier step abended"}})
                break

    # -------------------------------------------------------- results --
    def collect(self, result: Path, header: dict) -> dict:
        (result / "datasets").mkdir(parents=True)
        (result / "sysout").mkdir()
        files: dict[str, dict] = {}
        for p in sorted(self.sysout.iterdir()):
            data = p.read_bytes()
            if p.suffix == ".stderr" and not data:
                continue
            (result / "sysout" / p.name).write_bytes(data)
            files[f"sysout/{p.name}"] = {"bytes": len(data), "sha256": sha256(data)}
        datasets = {}
        for dsn, ds in sorted(self.datasets.items()):
            if ds.org == "KSDS":
                cl = self.cat.clusters[dsn]
                exe = build.ksds_utility("unload", cl.rec_max, cl.keyoff, cl.keylen)
                tmp = self.work / "unload" / dsn
                tmp.parent.mkdir(exist_ok=True)
                out = subprocess.run([str(exe)], cwd=self.work, capture_output=True,
                                     env={**self.base_env(), "DD_KSDSFILE": str(ds.path),
                                          "DD_SEQFILE": str(tmp)})
                if out.returncode != 0:
                    raise RunError(f"unloading {dsn} failed: {out.stdout!r} {out.stderr!r}")
                data = tmp.read_bytes()
            else:
                data = ds.path.read_bytes()
            info = {"org": ds.org, "recfm": ds.recfm, "lrecl": ds.lrecl,
                    "bytes": len(data), "sha256": sha256(data)}
            info.update(record_stats(data, ds))
            if ds.origin == "seed":
                info["seed"] = ds.seed_file
                info["role"] = "unchanged input" if data == ds.seed_image else "updated"
            else:
                info["role"] = "created"
            if info["role"] != "unchanged input":
                name = dsn + (".unload" if ds.org == "KSDS" else "")
                (result / "datasets" / name).write_bytes(data)
                info["file"] = f"datasets/{name}"
                if ds.org == "KSDS":
                    info["note"] = "KSDS unloaded to fixed-length records in key order"
            datasets[dsn] = info
        manifest = {**header, "steps": self.steps, "seed_loads": self.loads,
                    "datasets": datasets, "files": files}
        (result / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        return manifest


def record_stats(data: bytes, ds: Dataset) -> dict:
    recfm = (ds.recfm or "").upper()
    if recfm.startswith("V"):
        # GnuCOBOL COB_VARSEQ_FORMAT=0: 2-byte big-endian data length + 2 NULs
        n, i, lens = 0, 0, set()
        while i < len(data):
            if i + 4 > len(data) or data[i + 2:i + 4] != b"\0\0":
                return {"records": None, "error": f"bad record prefix at offset {i}"}
            ln = int.from_bytes(data[i:i + 2], "big")
            i += 4 + ln
            n += 1
            lens.add(ln)
        if i != len(data):
            return {"records": None, "error": "truncated last record"}
        out = {"records": n, "record_lengths": sorted(lens),
               "format": "GnuCOBOL varseq 0: 2-byte BE length (data only) + X'0000'"}
        if ds.lrecl and max(lens, default=0) > ds.lrecl - 4:
            out["error"] = f"record longer than LRECL-4 ({ds.lrecl - 4})"
        return out
    if ds.lrecl:
        if len(data) % ds.lrecl:
            return {"records": None, "error": f"size {len(data)} not a multiple of LRECL"}
        return {"records": len(data) // ds.lrecl}
    return {}


def clock_check(epoch: int, env: dict[str, str], cwd: Path) -> str:
    chk = build.UTIL / "CLOCKCHK"
    out = subprocess.run([str(chk)], cwd=cwd, env=env, capture_output=True)
    got = out.stdout.decode().strip()
    want = dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y%m%d%H%M%S") + "00+0000"
    if got != want:
        raise RunError(f"frozen clock not in effect: CURRENT-DATE={got!r}, expected {want!r}\n"
                       + out.stderr.decode(errors="replace"))
    return got


def compare_trees(a: Path, b: Path) -> list[str]:
    diffs = []
    fa = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
    fb = sorted(p.relative_to(b) for p in b.rglob("*") if p.is_file())
    for missing in sorted(set(fa) ^ set(fb)):
        diffs.append(f"only in one run: {missing}")
    for rel in sorted(set(fa) & set(fb)):
        if not filecmp.cmp(a / rel, b / rel, shallow=False):
            diffs.append(f"differs: {rel}")
    return diffs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("job", help="JCL member name, e.g. READACCT (legacy/app/jcl/<job>.jcl)")
    ap.add_argument("--runs", type=int, default=2, help="identical runs required (default 2)")
    ap.add_argument("--clock", default=DEFAULT_CLOCK, help=f"frozen UTC time (default {DEFAULT_CLOCK})")
    args = ap.parse_args(argv)
    if args.runs < 1:
        ap.error("--runs must be >= 1")

    job_name = args.job.upper()
    jcl_path = find_job(job_name)
    if jcl_path is None:
        print(f"no such job: {job_name} in {JCL_DIR.relative_to(REPO)}", file=sys.stderr)
        return 2
    clock = dt.datetime.fromisoformat(args.clock.replace("Z", "+00:00"))
    if clock.utcoffset() is None:
        ap.error("--clock needs a time zone, e.g. 2026-01-15T12:00:00Z")
    epoch = int(clock.timestamp())
    if clock.microsecond:
        ap.error("--clock must be whole seconds")

    job = parse_jcl(jcl_path)
    cat = build_catalog()
    programs = {s.pgm: build.compile_program(s.pgm) for s in job.steps if s.pgm not in NO_OP_PGMS}
    stubs = build.build_stubs()
    build.build_clock()

    header = {
        "job": job_name,
        "jcl": {"file": str(jcl_path.relative_to(REPO)), "sha256": sha256(jcl_path.read_bytes())},
        "legacy_commit": LEGACY_COMMIT,
        "toolchain": build.toolchain(),
        "cobc_flags": build.COBC_FLAGS,
        "programs": {k: {"source": v["source"], "copybooks": v["copybooks"],
                         "inputs_sha256": v["inputs_sha256"], "compile_warnings": v["warnings"]}
                     for k, v in programs.items()},
        "stubs": {k: v["inputs_sha256"] for k, v in stubs.items()},
        "frozen_clock": {"utc": clock.strftime("%Y-%m-%dT%H:%M:%SZ"), "epoch": epoch},
        "runtime_env": {k: v for k, v in JobRun.base_env().items()
                        if k not in ("PATH", "COB_LIBRARY_PATH")},
    }

    root = WORK / job_name
    shutil.rmtree(root, ignore_errors=True)
    results, manifests = [], []
    for n in range(1, args.runs + 1):
        wd = root / f"run{n}"
        wd.mkdir(parents=True)
        run = JobRun(job, cat, wd, epoch)
        header["clock_check"] = clock_check(epoch, run.program_env(), wd)
        try:
            run.run(programs)
        except NeedsUpstream as e:
            print(f"{job_name}: blocked: {e}", file=sys.stderr)
            return 3
        res = wd / "result"
        manifests.append(run.collect(res, header))
        results.append(res)
        status = ", ".join(f"{s['step']}={json.dumps(s['result'])}" for s in run.steps)
        print(f"run {n}: {status}")

    diffs = [d for r in results[1:] for d in compare_trees(results[0], r)]
    if diffs:
        print(f"{job_name}: runs are NOT identical; golden not written", file=sys.stderr)
        for d in diffs:
            print("  " + d, file=sys.stderr)
        return 1
    failed = [s for s in manifests[0]["steps"] if s.get("abended") or s["result"].get("rc", 0) > 4]
    dest = GOLDEN / job_name
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(results[0], dest)
    m = json.loads((dest / "manifest.json").read_text())
    m["verification"] = {"identical_runs": args.runs,
                         "compared": sorted(str(p.relative_to(results[0]))
                                            for p in results[0].rglob("*") if p.is_file())}
    (dest / "manifest.json").write_text(json.dumps(m, indent=2, sort_keys=True) + "\n")
    print(f"{job_name}: {args.runs} runs byte-identical -> {dest.relative_to(REPO)}/")
    for dsn, d in m["datasets"].items():
        print(f"  {d['role']:16} {dsn}  records={d.get('records')}  sha256={d['sha256'][:12]}")
    if failed:
        print(f"{job_name}: WARNING: step(s) failed: {[s['step'] for s in failed]}", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
