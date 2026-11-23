"""Dataset catalog for legacy runs.

VSAM cluster attributes (keys, record size) and seed lineage are read from
CardDemo's own IDCAMS JCL (DEFINE CLUSTER / REPRO), so they come from the
source.  The hand-written tables are SEEDS (which file in
legacy/app/data/ASCII holds each uploaded PS dataset, and its LRECL) and
RAW_SEEDS (datasets taken byte-for-byte from legacy/app/data/EBCDIC).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .jcl import UnsupportedJCL, idcams_commands, parse_jcl

REPO = Path(__file__).resolve().parents[1]
LEGACY_APP = REPO / "legacy" / "app"
JCL_DIR = LEGACY_APP / "jcl"
ASCII_DIR = LEGACY_APP / "data" / "ASCII"
EBCDIC_DIR = LEGACY_APP / "data" / "EBCDIC"

# PS dataset -> (file in data/ASCII, LRECL).  Each LRECL equals the size of
# the matching file in data/EBCDIC divided by its record count; see
# docs/legacy-setup.md D1-D3 for how the ASCII files differ from EBCDIC.
SEEDS: dict[str, tuple[str, int]] = {
    "AWS.M2.CARDDEMO.ACCTDATA.PS": ("acctdata.txt", 300),
    "AWS.M2.CARDDEMO.CARDDATA.PS": ("carddata.txt", 150),
    "AWS.M2.CARDDEMO.CARDXREF.PS": ("cardxref.txt", 50),
    "AWS.M2.CARDDEMO.CUSTDATA.PS": ("custdata.txt", 500),
    "AWS.M2.CARDDEMO.DALYTRAN.PS": ("dailytran.txt", 350),
    "AWS.M2.CARDDEMO.DISCGRP.PS": ("discgrp.txt", 50),
    "AWS.M2.CARDDEMO.TCATBALF.PS": ("tcatbal.txt", 50),
    "AWS.M2.CARDDEMO.TRANCATG.PS": ("trancatg.txt", 60),
    "AWS.M2.CARDDEMO.TRANTYPE.PS": ("trantype.txt", 60),
}

# Datasets with no ASCII seed, loaded byte-for-byte from data/EBCDIC (W15).
# DALYTRAN.PS.INIT is the single 350-byte priming record TRANFILE.jcl REPROs
# into TRANSACT.VSAM.KSDS: low-value key and body, ending in EBCDIC '00000100'.
RAW_SEEDS: dict[str, tuple[str, int]] = {
    "AWS.M2.CARDDEMO.DALYTRAN.PS.INIT": ("AWS.M2.CARDDEMO.DALYTRAN.PS.INIT", 350),
}


@dataclass
class Cluster:
    dsn: str
    org: str                 # KSDS | ESDS | RRDS | LDS
    keylen: int | None
    keyoff: int | None
    rec_avg: int
    rec_max: int
    defined_in: str
    aix: list[str] = field(default_factory=list)


@dataclass
class Catalog:
    clusters: dict[str, Cluster]
    repro: dict[str, tuple[str, str]]      # target DSN -> (source DSN, jcl file)
    gdg_bases: dict[str, str]              # base DSN -> jcl file
    # target DSN -> every (source DSN, jcl file) when JCL files disagree; the
    # first file in name order wins, and the runner refuses to seed from these
    repro_conflicts: dict[str, set[tuple[str, str]]] = field(default_factory=dict)

    def seed_for(self, dsn: str) -> tuple[Path, int, str] | None:
        """(file, LRECL, mode): mode 'text' (ASCII lines) or 'raw' (FB bytes)."""
        if dsn in SEEDS:
            name, lrecl = SEEDS[dsn]
            return ASCII_DIR / name, lrecl, "text"
        if dsn in RAW_SEEDS:
            name, lrecl = RAW_SEEDS[dsn]
            return EBCDIC_DIR / name, lrecl, "raw"
        return None


def jcl_files(jcl_dir: Path = JCL_DIR) -> list[Path]:
    """All JCL members; upstream uses both .jcl and .JCL."""
    return sorted(p for p in jcl_dir.iterdir() if p.suffix.lower() == ".jcl")


def find_job(name: str, jcl_dir: Path = JCL_DIR) -> Path | None:
    return next((p for p in jcl_files(jcl_dir) if p.stem.upper() == name.upper()), None)


def _num_pair(cmd: str, kw: str) -> tuple[int, int] | None:
    m = re.search(kw + r"\(\s*(\d+)[\s,]+(\d+)\s*\)", cmd)
    return (int(m.group(1)), int(m.group(2))) if m else None


def build_catalog(jcl_dir: Path = JCL_DIR) -> Catalog:
    clusters: dict[str, Cluster] = {}
    repro: dict[str, tuple[str, str]] = {}
    gdg: dict[str, str] = {}
    aix_rel: list[tuple[str, str]] = []
    conflicts: dict[str, set[tuple[str, str]]] = {}
    for path in jcl_files(jcl_dir):
        try:
            job = parse_jcl(path)
        except UnsupportedJCL:
            continue
        for step in job.steps:
            if step.pgm != "IDCAMS":
                continue
            sysin = step.dd("SYSIN")
            if sysin is None or sysin.instream is None:
                continue
            for cmd in idcams_commands(sysin.instream):
                if cmd.startswith("DEFINE CLUSTER"):
                    name = re.search(r"NAME\(([^)\s]+)\)", cmd).group(1)
                    keys = _num_pair(cmd, "KEYS")
                    rs = _num_pair(cmd, "RECORDSIZE")
                    org = ("ESDS" if " NONINDEXED" in cmd else
                           "RRDS" if " NUMBERED" in cmd else
                           "LDS" if " LINEAR" in cmd else "KSDS")
                    if rs is None:
                        raise UnsupportedJCL(f"{path.name}: {name} has no RECORDSIZE")
                    prev = clusters.get(name)
                    new = (org, keys, rs)
                    if prev and (prev.org, (prev.keylen, prev.keyoff) if prev.keylen is not None
                                 else None, (prev.rec_avg, prev.rec_max)) != new:
                        raise UnsupportedJCL(f"{name}: defined differently in "
                                             f"{prev.defined_in} and {path.name}")
                    clusters[name] = Cluster(
                        dsn=name, org=org,
                        keylen=keys[0] if keys else None,
                        keyoff=keys[1] if keys else None,
                        rec_avg=rs[0], rec_max=rs[1], defined_in=path.name)
                elif cmd.startswith("DEFINE ALTERNATEINDEX"):
                    name = re.search(r"NAME\(([^)\s]+)\)", cmd).group(1)
                    rel = re.search(r"RELATE\(([^)\s]+)\)", cmd)
                    if rel:
                        aix_rel.append((rel.group(1), name))
                elif cmd.startswith(("DEFINE GDG", "DEFINE GENERATIONDATAGROUP")):
                    name = re.search(r"NAME\(([^)\s]+)\)", cmd).group(1)
                    gdg[name] = path.name
                elif cmd.startswith("REPRO"):
                    src = re.search(r"IN(?:FILE|DATASET)\(([^)\s]+)\)", cmd)
                    dst = re.search(r"OUT(?:FILE|DATASET)\(([^)\s]+)\)", cmd)
                    if not (src and dst):
                        continue
                    s, d = src.group(1), dst.group(1)
                    if "INFILE" in cmd:
                        dd = step.dd(s)
                        s = dd.dsn if dd else s
                    if "OUTFILE" in cmd:
                        dd = step.dd(d)
                        d = dd.dsn if dd else d
                    if d in repro and repro[d][0] != s:
                        conflicts.setdefault(d, {repro[d]}).add((s, path.name))
                    repro.setdefault(d, (s, path.name))
    for base, aix in aix_rel:
        if base in clusters and aix not in clusters[base].aix:
            clusters[base].aix.append(aix)
    return Catalog(clusters=clusters, repro=repro, gdg_bases=gdg, repro_conflicts=conflicts)


def load_seed(path: Path, lrecl: int, mode: str) -> tuple[bytes, dict]:
    if mode == "text":
        return fb_from_seed(path, lrecl)
    if mode != "raw":
        raise ValueError(mode)
    data = path.read_bytes()
    if len(data) % lrecl:
        raise ValueError(f"{path.name}: {len(data)} bytes is not a multiple of LRECL {lrecl}")
    return data, {"records": len(data) // lrecl, "raw_bytes": True}


def fb_from_seed(path: Path, lrecl: int) -> tuple[bytes, dict]:
    """Seed text -> fixed-block bytes, as an FTP/IND$FILE text upload would
    produce: one record per line, trailing CR removed, blank-padded to LRECL.
    Lines longer than LRECL are an error (never truncated)."""
    raw = path.read_bytes()
    lines = raw.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    stats = {"records": len(lines), "crlf": 0, "padded": 0}
    out = bytearray()
    for i, ln in enumerate(lines, 1):
        if ln.endswith(b"\r"):
            ln = ln[:-1]
            stats["crlf"] += 1
        if len(ln) > lrecl:
            raise ValueError(f"{path.name}:{i}: {len(ln)} bytes > LRECL {lrecl}")
        if len(ln) < lrecl:
            stats["padded"] += 1
        out += ln.ljust(lrecl, b" ")
    return bytes(out), stats
