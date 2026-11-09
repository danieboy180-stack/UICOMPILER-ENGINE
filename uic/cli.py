"""python -m uic <command>  (run from the repo root so `ingest`, `ingest_layer342` and `orchestrator` import).

  prompts                      fingerprints of the stored prompts
  lint  --kind 12|342 FILE     executable prompt contract on a raw bundle
  pack  --kind K --in F --out D  byte-exact packaging (replaces the LLM packager prompts)
  run   --root R --source S --l12 F [--l342 F] [--project P] [--export DIR]
  run   --root R --l342 F      continue the newest run that has L1+L2 but no L3
  export --root R --out DIR [--task T] [--require-source]
  verify DIR
  demo  --root R               generate a conformant sample and run the whole lane (smoke test)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from . import UIC_VERSION, spec  # noqa: E402
from .bundle import chash, pack, parse_tags, load_body, sha256_bytes, validate_pack  # noqa: E402
from .check import check_bundle12, check_ir, has_errors, verify_chains  # noqa: E402
from .export import TASKS, export_dataset, verify_dataset  # noqa: E402
from .seal import l2_hash_from_disk, seal12, seal342  # noqa: E402

EXIT_OK, EXIT_LINT, EXIT_INGEST, EXIT_PIPELINE = 0, 2, 3, 4


def _print(findings) -> None:
    for f in findings:
        print(f"  {f}")


def _ctx_from(l1: dict, l2: dict) -> dict:
    from .check import _static, l1_objects, walk
    ids2 = {v for _, d in walk(l2) for k in spec.L2_STRUCTURAL_ID_KEYS if isinstance((v := d.get(k)), str)}
    return {"l1_ids": {o.get("id") for o in l1_objects(l1)}, "l2_ids": ids2, "static": _static(l1)}


def lint342(text: str, ctx: dict, root: Path | None = None) -> list:
    tags = parse_tags(text, ("LAYER3_OUTPUT", "LAYER4_1_OUTPUT", "LAYER4_2_OUTPUT"))
    out, bodies = [], {}
    for layer, tag in (("L3", "LAYER3_OUTPUT"), ("L41", "LAYER4_1_OUTPUT"), ("L42", "LAYER4_2_OUTPUT")):
        if tag in tags:
            body, err = load_body(tags[tag])
            if body is None:
                from .check import Finding
                out.append(Finding(f"{layer}.PARSE", "error", tag, err or "invalid json"))
                continue
            bodies[layer] = body
            out += check_ir(layer, body, ctx)
    if root is not None and bodies:
        out += verify_chains(bodies, l2_hash_from_disk(root))
    return out


def pending_run(root: Path) -> str | None:
    best = None
    for mf in (root / "Manifest").glob("manifest_*.json"):
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        a = m.get("artifacts", {})
        if a.get("layer2_csir") and not a.get("layer3_visual_ir"):
            if best is None or mf.stat().st_mtime > best[0]:
                best = (mf.stat().st_mtime, m.get("run_id") or mf.stem[len("manifest_"):])
    return best[1] if best else None


def run_lane(root: Path, source: Path | None, l12: Path | None, l342: Path | None, project: str,
             export_dir: Path | None = None, force_lint: bool = False) -> int:
    import ingest
    import ingest_layer342
    from orchestrator.lock import simple_file_lock
    from orchestrator.orchestrator import Pipeline, PipelineContext
    root.mkdir(parents=True, exist_ok=True)
    with simple_file_lock(root / ".uic_run", timeout=600):
        run_id = None
        if l12:
            if source is None:
                print("run: --source (screenshot or code file) is required with --l12")
                return EXIT_LINT
            src_bytes = source.read_bytes()
            src_hash = sha256_bytes(src_bytes)
            raw = l12.read_bytes()
            text = raw.decode("utf-8")
            findings, _ = check_bundle12(text)
            print(f"[lint L1+2] {len(findings)} finding(s)")
            _print(findings)
            if has_errors(findings) and not force_lint:
                print("run: REFUSED (fix the bundle or pass --force-lint to ingest anyway)")
                return EXIT_LINT
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            raw_dir = root / "Raw" / f"{stamp}_{src_hash[:12]}"
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / "layer12_bundle.raw.txt").write_bytes(raw)
            sealed, srep = seal12(text, src_hash, project)
            (raw_dir / "seal12_report.json").write_text(json.dumps(srep, indent=2), encoding="utf-8")
            kind = "image" if source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"} else "code"
            store = root / ("Images" if kind == "image" else "Sources")
            store.mkdir(parents=True, exist_ok=True)
            dest = store / f"{src_hash}{source.suffix.lower() or '.txt'}"
            if not dest.exists():
                shutil.copyfile(source, dest)
            prep = pack("12", sealed.encode("utf-8"), root / "Python_Inbox")
            print(prep.summary())
            if not prep.ok:
                return EXIT_LINT
            code, run_id, outcome = ingest.ingest_bundle(root)
            print(f"[ingest L1+2] code={code} outcome={outcome} run_id={run_id}")
            if outcome != "ingested":
                return EXIT_INGEST
            print(f"[next] send the layer342 bundle: python -m uic run --root {root} --l342 <file>")
        if l342:
            run_id = run_id or pending_run(root)
            if not run_id:
                print("run: no pending run (L1+2 ingested, L3 missing) found")
                return EXIT_INGEST
            l1 = json.loads((root / "Artifacts" / "layer1_ccir.json").read_text(encoding="utf-8"))
            l2 = json.loads((root / "Artifacts" / "layer2_csir.json").read_text(encoding="utf-8"))
            text = l342.read_bytes().decode("utf-8")
            findings = lint342(text, _ctx_from(l1, l2))
            print(f"[lint L3+4.1+4.2] {len(findings)} finding(s)")
            _print(findings)
            if has_errors(findings) and not force_lint:
                print("run: REFUSED (contract errors in layer342 bundle)")
                return EXIT_LINT
            raw_dir = root / "Raw" / f"{run_id}"
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / "layer342_bundle.raw.txt").write_bytes(l342.read_bytes())
            sealed, srep = seal342(text, l2_hash_from_disk(root), run_id)
            (raw_dir / "seal342_report.json").write_text(json.dumps(srep, indent=2), encoding="utf-8")
            post = lint342(sealed, _ctx_from(l1, l2), root)
            if has_errors(post):
                print("run: sealed bundle still violates the contract:")
                _print(post)
                return EXIT_LINT
            prep = pack("342", sealed.encode("utf-8"), root / "Python_Inbox")
            print(prep.summary())
            if not prep.ok:
                return EXIT_LINT
            code = ingest_layer342.ingest(root)
            print(f"[ingest L3+4.1+4.2] code={code}")
            if code != 0:
                return EXIT_INGEST
            ctx = PipelineContext(run_id=run_id, manifest_path=root / "Manifest" / f"manifest_{run_id}.json")
            rc = Pipeline().run(ctx)
            print(f"[pipeline] rc={rc} status={ctx.storage.get('pipeline_status')} errors={ctx.errors}")
            if rc != 0:
                return EXIT_PIPELINE
            if export_dir:
                man = export_dataset(root, export_dir, force=True)
                print(f"[export] {man['counts']}")
                bad = verify_dataset(export_dir)
                print(f"[verify] {'clean' if not bad else bad}")
                return EXIT_OK if not bad else EXIT_PIPELINE
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="uic", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=UIC_VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prompts")
    p = sub.add_parser("lint"); p.add_argument("--kind", choices=["12", "342"], required=True); p.add_argument("file", type=Path)
    p.add_argument("--l12", type=Path, help="layer12 bundle (gives 342 lint its id context)")
    p.add_argument("--root", type=Path, help="project root: also verify hash chains against Artifacts/layer2_csir.json")
    p = sub.add_parser("pack"); p.add_argument("--kind", choices=["12", "342"], required=True)
    p.add_argument("--in", dest="inp", type=Path, required=True); p.add_argument("--out", type=Path, required=True)
    p.add_argument("--allow-partial", action="store_true")
    p = sub.add_parser("run"); p.add_argument("--root", type=Path, default=Path.cwd()); p.add_argument("--source", type=Path)
    p.add_argument("--l12", type=Path); p.add_argument("--l342", type=Path); p.add_argument("--project", default="default_project")
    p.add_argument("--export", type=Path); p.add_argument("--force-lint", action="store_true")
    p = sub.add_parser("export"); p.add_argument("--root", type=Path, default=Path.cwd()); p.add_argument("--out", type=Path, required=True)
    p.add_argument("--task", choices=sorted(TASKS)); p.add_argument("--require-source", action="store_true")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("verify"); p.add_argument("dir", type=Path)
    p = sub.add_parser("demo"); p.add_argument("--root", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "prompts":
        for name, h in spec.prompt_fingerprints().items():
            print(f"{h}  {name}")
        return EXIT_OK
    if a.cmd == "lint":
        text = a.file.read_bytes().decode("utf-8")
        if a.kind == "12":
            findings, _ = check_bundle12(text)
        else:
            ctx = {}
            if a.l12:
                _, parts = check_bundle12(a.l12.read_bytes().decode("utf-8"))
                ctx = _ctx_from(parts["l1"], parts["l2"]) if parts else {}
            findings = lint342(text, ctx, a.root)
        _print(findings)
        print(f"{sum(f.level == 'error' for f in findings)} error(s), {sum(f.level == 'warn' for f in findings)} warning(s)")
        return EXIT_LINT if has_errors(findings) else EXIT_OK
    if a.cmd == "pack":
        rep = pack(a.kind, a.inp.read_bytes(), a.out, a.allow_partial)
        print(rep.summary())
        return EXIT_OK if rep.ok else EXIT_LINT
    if a.cmd == "run":
        return run_lane(a.root.resolve(), a.source, a.l12, a.l342, a.project, a.export, a.force_lint)
    if a.cmd == "export":
        man = export_dataset(a.root, a.out, a.task, a.require_source, force=a.force)
        print(json.dumps(man["counts"]))
        bad = verify_dataset(a.out)
        print("verify:", "clean" if not bad else bad)
        return EXIT_OK if not bad else EXIT_PIPELINE
    if a.cmd == "verify":
        bad = verify_dataset(a.dir)
        print("clean" if not bad else "\n".join(bad))
        return EXIT_OK if not bad else EXIT_PIPELINE
    if a.cmd == "demo":
        from . import fixtures as fx
        root = a.root.resolve()
        work = root / "_demo_inputs"
        work.mkdir(parents=True, exist_ok=True)
        s = fx.build_sample(project="demo")
        (work / "source.png").write_bytes(b"demo-source:" + hashlib.sha256(str(root).encode()).digest())
        (work / "l12.txt").write_text(fx.text12(s), encoding="utf-8")
        (work / "l342.txt").write_text(fx.text342(s), encoding="utf-8")
        rc = run_lane(root, work / "source.png", work / "l12.txt", work / "l342.txt", "demo", root / "Datasets" / "demo", True)
        print("DEMO", "PASSED" if rc == EXIT_OK else f"FAILED rc={rc}")
        return rc
    return EXIT_OK
