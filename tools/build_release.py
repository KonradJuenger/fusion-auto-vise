"""Build an installable add-in archive without local settings or debug files."""
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
ADDIN = ROOT / 'AutoVise'
FILES = (
    'AutoVise.py', 'AutoVise.manifest', 'parallels.json',
    'autovise_commands.py', 'autovise_geometry.py', 'autovise_model.py',
    'autovise_roles.py', 'autovise_rules.py', 'autovise_service.py',
    'autovise_support.py',
)


def build():
    version = json.loads((ADDIN / 'AutoVise.manifest').read_text(encoding='utf-8'))['version']
    files = [ADDIN / name for name in FILES]
    files += sorted(p for p in (ADDIN / 'Resources').rglob('*') if p.is_file())
    for path in files:
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.suffix == '.py':
            compile(path.read_text(encoding='utf-8-sig'), str(path), 'exec')
    destination = ROOT / 'dist' / f'AutoVise-{version}.zip'
    destination.parent.mkdir(exist_ok=True)
    with ZipFile(destination, 'w', ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT).as_posix())
        if (ROOT / 'LICENSE').is_file():
            archive.write(ROOT / 'LICENSE', 'LICENSE')
    print(destination)
    return destination


if __name__ == '__main__':
    build()
