"""Rebuild the ui-samples bundles from the authored specs (run from the repo root):
    python tools/author/build.py [--src-dir _inbox] [--out bundles/ui-samples] [--crop-dir Images]
Each spec crops one phone screen out of an original upload, then compiles the hand-authored object boxes into the
Layer 1+2 and Layer 3/4.1/4.2 raw bundles. Needs: pip install pillow numpy"""
import argparse, importlib, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen
SPECS = ["spec_fit_home", "spec_fit_stats", "spec_fin_onboarding", "spec_fin_home", "spec_fin_stats"]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src-dir", default="_inbox"); ap.add_argument("--out", default="bundles/ui-samples"); ap.add_argument("--crop-dir", default="Images")
    a = ap.parse_args()
    for m in SPECS:
        S = importlib.import_module(m).S
        tmp = os.path.join(a.crop_dir, "_build", S["name"])
        png, ih, l1, l2 = gen.make(S, tmp, a.src_dir, a.crop_dir)
        d = os.path.join(a.out, ih[:12]); os.makedirs(d, exist_ok=True)
        os.replace(os.path.join(tmp, "l12.txt"), os.path.join(d, "layer12_bundle.raw.txt"))
        os.replace(os.path.join(tmp, "l342.txt"), os.path.join(d, "layer342_bundle.raw.txt"))
        print(S["name"], ih[:12], len(l1["objects"]), "objects")

if __name__ == "__main__":
    main()
