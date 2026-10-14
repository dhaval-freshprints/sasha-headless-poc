"""Install the two companion skills without replacing existing skill folders."""
import argparse
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, default=Path.home() / '.codex' / 'skills')
    args = parser.parse_args()
    source = Path(__file__).resolve().parent / 'skills'
    names = ('deal-manager-database', 'deal-manager-resolve-deal')
    destination = args.destination.expanduser()
    for name in names:
        if (destination / name).exists():
            raise SystemExit(f'Refusing to replace existing skill: {destination / name}')
    destination.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copytree(source / name, destination / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        if name == 'deal-manager-database':
            shutil.copytree(source.parent.parent / 'sasha_database',
                            destination / name / 'scripts' / 'sasha_database',
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        print(destination / name / 'SKILL.md')


if __name__ == '__main__':
    main()
