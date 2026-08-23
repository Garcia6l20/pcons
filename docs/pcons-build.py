# SPDX-License-Identifier: MIT
"""Build the pcons documentation site, with pcons.

The site is mkdocs (mkdocs.yml at the repo root); ReadTheDocs and
`make docs-site` invoke mkdocs themselves. This wraps the same build so
`pcons -C docs` produces it too, into docs/build/site/, and rebuilds only
when a page or the configuration changed.

The custom-tools pipeline that used to live here is now
examples/69_custom_tools_pipeline.
"""

import sys
from pathlib import Path

from pcons import Project

project = Project("pcons-docs")
env = project.Environment()

# Every page is an input, so an edit rebuilds the site and an untouched
# tree is a no-op. Globbed at configure time: a brand-new page appears on
# the next pcons run (page *edits* need only ninja). Resolved, because
# some pages are symlinks (architecture.md -> ../ARCHITECTURE.md) and the
# real dependency is the file behind the link.
pages = sorted(p.resolve() for p in Path(".").glob("*.md"))
config = [Path("../mkdocs.yml").resolve(), Path("macros.py").resolve()]

# The site goes where every other way of building it puts it: site/ at
# the repo root (gitignored). It cannot live under docs/ — mkdocs refuses
# a site_dir inside its docs_dir — and mkdocs resolves -d relative to the
# config file, not the working directory, so the location is absolute.
site_dir = Path("../site").resolve()

site = env.Command(
    target=site_dir / "sitemap.xml",
    source=[*pages, *config],
    command=[
        sys.executable,
        "-m",
        "mkdocs",
        "build",
        "--strict",
        "-f",
        "$SRCDIR/../mkdocs.yml",
        "-d",
        str(site_dir),
    ],
    name="site",
)

project.Default(site)


@project.cli_command()
def showdocs() -> None:
    """Serve the documentation site in a browser, rebuilding on edits.

    Serves over HTTP rather than opening the files directly, because
    mkdocs-material needs a real server for parts of its behavior. With
    the optional ``watchfiles`` package installed, editing a page
    rebuilds the site; refresh the browser to see it.
    """
    import functools
    import http.server
    import subprocess
    import threading
    import webbrowser

    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass  # request logging would drown the build output

    handler = functools.partial(QuietHandler, directory=str(site_dir))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Serving docs at {url}  (Ctrl-C to stop)")
    webbrowser.open(url)

    def rebuild() -> int:
        return subprocess.run(
            [sys.executable, "-m", "pcons", "build"], cwd=project.root_dir
        ).returncode

    from pcons import watch

    try:
        watch.ensure_available()
    except Exception as e:
        print(f"Not watching for edits: {e}")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            return
    watch.watch_and_build(
        rebuild,
        [project.root_dir],
        excluded_dirs=[project.root_dir / "build"],
    )


showdocs.depends(site)
