import glob
import os
import subprocess


class ModeDetector:
    @staticmethod
    def detect(graph_dir, project_dir):
        graph_state = ModeDetector._graph_state(graph_dir)
        has_existing_data = ModeDetector._has_existing_reports(project_dir)
        java_changes = ModeDetector._git_java_changes(project_dir)

        if graph_state == "UNINITIALIZED" and not has_existing_data:
            return "BOOTSTRAP"
        if graph_state == "UNINITIALIZED" and has_existing_data:
            return "BOOTSTRAP_WITH_DATA"
        if graph_state == "INITIALIZED":
            mode_file = os.path.join(graph_dir, "mode.json")
            if os.path.exists(mode_file):
                import json
                try:
                    with open(mode_file) as f:
                        saved = json.load(f)
                    if isinstance(saved, dict) and saved.get("mode") == "COMPLETE":
                        return "INCREMENTAL" if java_changes else "COVERAGE_ONLY"
                except (json.JSONDecodeError, OSError):
                    pass
        if java_changes:
            return "INCREMENTAL"
        if has_existing_data:
            return "COVERAGE_ONLY"
        return "INCREMENTAL"

    @staticmethod
    def _graph_state(graph_dir):
        manifest = os.path.join(graph_dir, "manifest.json")
        if not os.path.exists(manifest):
            return "UNINITIALIZED"
        try:
            import json
            with open(manifest) as f:
                data = json.load(f)
            if not data or data.get("methodCount", 0) == 0:
                return "UNINITIALIZED"
        except (json.JSONDecodeError, OSError):
            return "UNINITIALIZED"
        return "INITIALIZED"

    @staticmethod
    def _has_existing_reports(project_dir):
        jacoco = os.path.join(project_dir, "target/site/jacoco/jacoco.xml")
        pit = glob.glob(os.path.join(project_dir, "target/pit-reports", "*", "mutations.xml"))
        return os.path.exists(jacoco) or bool(pit)

    @staticmethod
    def _git_java_changes(project_dir):
        try:
            parent = ModeDetector._parent_commit(project_dir)
            if not parent:
                return False
            curr = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=project_dir, stderr=subprocess.DEVNULL, text=True,
            ).strip()
            diff = subprocess.check_output(
                ["git", "diff", "--name-only", parent, curr],
                cwd=project_dir, stderr=subprocess.DEVNULL, text=True,
            )
            return any(f.endswith(".java") for f in diff.splitlines())
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    @staticmethod
    def _parent_commit(project_dir):
        try:
            output = subprocess.check_output(
                ["git", "rev-list", "--parents", "HEAD"],
                cwd=project_dir, stderr=subprocess.DEVNULL, text=True,
            )
            lines = [l.strip() for l in output.strip().splitlines() if l.strip()]
            if not lines:
                return None
            commits = lines[0].split()
            return commits[1] if len(commits) >= 2 else None
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None

    @staticmethod
    def get_commit_hash(project_dir):
        try:
            return subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=project_dir, stderr=subprocess.DEVNULL, text=True,
            ).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            import hashlib
            return hashlib.sha256(project_dir.encode()).hexdigest()[:12]
