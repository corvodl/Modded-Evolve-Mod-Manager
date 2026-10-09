"""Run the selected helper with its original file location and journal paths."""
import runpy
import sys
from pathlib import Path

def main():
    if len(sys.argv) < 2:
        raise SystemExit('This is the background helper. Open EvolveModManager.exe.')
    script = Path(sys.argv[1]).resolve(strict=True)
    sys.argv = [str(script), *sys.argv[2:]]
    sys.path.insert(0, str(script.parent))
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.reconfigure(line_buffering=True, write_through=True)
    runpy.run_path(str(script), run_name='__main__')

if __name__ == '__main__':
    main()
