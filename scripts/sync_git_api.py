"""Mirror a local Git commit through GitHub Git Data API when port 443 Git fails.

No local file contents or auth tokens are printed. Creates immutable Git objects,
verifies their hashes, then updates only this newly created project's branch.
"""

import base64
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

root = Path(__file__).resolve().parents[1]
repository = "hjy8848/EvoTau-GEPA"


def git(*args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def api(path, payload=None, method=None):
    command = ["gh", "api", f"repos/{repository}/{path}".rstrip("/")]
    if payload is not None:
        command += ["--method", method or "POST", "--input", "-"]
    response = subprocess.run(
        command,
        input=json.dumps(payload).encode() if payload is not None else None,
        capture_output=True,
        check=False,
    )
    if response.returncode:
        raise RuntimeError(response.stderr.decode()[:500])
    return json.loads(response.stdout)


def upload_blob(entry):
    sha = entry["sha"]
    raw = git("cat-file", "blob", sha)
    response = api("git/blobs", {"content": base64.b64encode(raw).decode(), "encoding": "base64"})
    if response["sha"] != sha:
        raise ValueError("Git blob identity changed")
    return entry


def identity(text):
    name_email, timestamp, offset = text.rsplit(" ", 2)
    name, email = name_email.rsplit(" <", 1)
    sign = 1 if offset[0] == "+" else -1
    tz = timezone(sign * timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5])))
    return {
        "name": name,
        "email": email.removesuffix(">"),
        "date": datetime.fromtimestamp(int(timestamp), tz).isoformat(),
    }


def mirror(commit):
    raw = git("cat-file", "commit", commit).decode()
    header, message = raw.split("\n\n", 1)
    fields = [line.split(" ", 1) for line in header.splitlines()]
    parents = [value for key, value in fields if key == "parent"]
    for parent in parents:
        try:
            api("git/commits/" + parent)
        except RuntimeError:
            mirror(parent)
    tree_entries = []
    for line in git("ls-tree", "-rz", commit).split(b"\0"):
        if not line:
            continue
        meta, path = line.split(b"\t", 1)
        mode, kind, sha = meta.decode().split()
        tree_entries.append({"path": path.decode(), "mode": mode, "type": kind, "sha": sha})
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(upload_blob, tree_entries))
    tree = api("git/trees", {"tree": tree_entries})
    expected_tree = next(value for key, value in fields if key == "tree")
    if tree["sha"] != expected_tree:
        raise ValueError("Git tree identity changed")
    commit_body = {
        "message": message,
        "tree": tree["sha"],
        "parents": parents,
        "author": identity(next(value for key, value in fields if key == "author")),
        "committer": identity(next(value for key, value in fields if key == "committer")),
    }
    created = api("git/commits", commit_body)
    if created["sha"] != commit:
        raise ValueError(f"Commit identity differs; branch untouched: {created['sha']} != {commit}")
    return commit


if __name__ == "__main__":
    head = git("rev-parse", "HEAD").decode().strip()
    bootstrap_path = root / "results/github_bootstrap.json"
    try:
        api("git/ref/heads/main")
    except RuntimeError:
        metadata = api("")
        if metadata["size"] != 0:
            raise ValueError("Nonempty remote has no recognized main ref")
        response = api(
            "contents/README.md",
            {
                "message": "Initialize newly created EvoTau-GEPA repository",
                "content": base64.b64encode((root / "README.md").read_bytes()).decode(),
            },
            method="PUT",
        )
        bootstrap_path.write_text(json.dumps({"sha": response["commit"]["sha"]}))
    mirror(head)
    try:
        ref = api("git/ref/heads/main")
    except RuntimeError:
        api("git/refs", {"ref": "refs/heads/main", "sha": head})
    else:
        current = ref["object"]["sha"]
        if current != head:
            bootstrap = (
                json.loads(bootstrap_path.read_text())["sha"] if bootstrap_path.exists() else None
            )
            if current == bootstrap:
                api("git/refs/heads/main", {"sha": head, "force": True}, method="PATCH")
            elif subprocess.run(
                ["git", "-C", str(root), "merge-base", "--is-ancestor", current, head],
                capture_output=True,
                check=False,
            ).returncode:
                raise ValueError("Remote has unrecognized history; refusing overwrite")
            elif current != head:
                api("git/refs/heads/main", {"sha": head, "force": False}, method="PATCH")
    print(json.dumps({"repository": repository, "verified_commit": head}))
