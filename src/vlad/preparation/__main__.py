"""Internal headless derivative-preparation entry point."""

import argparse
import json
from pathlib import Path
import sys

from .bids import prepare_inputs, preview_inputs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--modality', required=True)
    parser.add_argument('--subjects', type=Path, help='Use this explicit selected subject-visit list instead of the whole cohort')
    parser.add_argument('--overwrite', action='store_true')
    parser.add_argument('--preview', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.preview:
            result = preview_inputs(args.config, args.modality, subjects_file=args.subjects)
            print(json.dumps(result, indent=2))
            return int(bool(result['errors'] or result['missing'] or result['ambiguous']))
        prepare_inputs(args.config, args.modality, overwrite=args.overwrite, subjects_file=args.subjects)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'Preparation failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
