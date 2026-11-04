"""Check that every file and directory name in a repository uses only lowercase ASCII letters, digits, '_', '-' and '.'.

    python check_repo_names.py [repo_dir]        # default: the current directory; exit code 1 if anything fails

Conventional upper-case names that tools look for by exact spelling (README.md, LICENSE, CMakeLists.txt) are allowed.
Only tracked files are checked when the directory is a git repository; otherwise everything under it except .git/.
"""
import os
import re
import subprocess
import sys

OK = re.compile(r'^[a-z0-9_.\-]+$')
ALLOWED = {'README.md', 'LICENSE', 'CMakeLists.txt'}


def paths(root):
    try:
        out = subprocess.run(['git', '-c', 'core.quotepath=false', 'ls-files'], cwd=root,
                             capture_output=True, text=True, encoding='utf-8', check=True).stdout
        return [p for p in out.splitlines() if p]
    except (OSError, subprocess.CalledProcessError):
        found = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d != '.git']
            rel = os.path.relpath(dirpath, root).replace(os.sep, '/')
            found += [n if rel == '.' else rel + '/' + n for n in filenames]
        return found


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else '.'
    bad = set()
    for p in paths(root):
        parts = p.split('/')
        for i, name in enumerate(parts):
            if name not in ALLOWED and not OK.match(name):
                bad.add('/'.join(parts[:i + 1]))
    for p in sorted(bad):
        print('BAD', p)
    print('%d bad names' % len(bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
