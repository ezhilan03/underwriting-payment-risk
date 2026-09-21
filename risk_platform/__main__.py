import argparse
import json


def main():
    parser = argparse.ArgumentParser(description="Synthetic underwriting and payment risk demo")
    commands = parser.add_subparsers(dest="command", required=True)
    batch = commands.add_parser("run")
    batch.add_argument("--output", default="artifacts/latest")
    batch.add_argument("--seed", type=int, default=42)
    batch.add_argument("--applications", type=int, default=3600)
    check = commands.add_parser("verify")
    check.add_argument("output")
    args = parser.parse_args()
    from .pipeline import run, verify
    if args.command == "run":
        result = run(args.output, args.seed, args.applications)
        print(json.dumps(dict(status="success", output=args.output, counts=result["counts"], selected_model=result["experiment"]["selected_model"]), sort_keys=True))
    else:
        result = verify(args.output)
        print(json.dumps(dict(status="verified", files=len(result["files"]))))


if __name__ == "__main__":
    main()
