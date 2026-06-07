import glob
import hashlib
import json
import os

import tree_sitter_java as tsjava
from tree_sitter import Language, Parser

from cfg_builder import CFGBuilder
from flow_analysis import DFGBuilder

JAVA_LANGUAGE = Language(tsjava.language())


class JavaParser:
    def __init__(self):
        self.parser = Parser(JAVA_LANGUAGE)
        self.cfg_builder = CFGBuilder()
        self.dfg_builder = DFGBuilder()

    def parse_project(self, project_dir):
        classes = []
        for f in glob.glob(os.path.join(project_dir, "**/*.java"), recursive=True):
            classes.append(self.parse_file(f))
        return classes

    def parse_changed_files(self, project_dir, changed_files):
        results = []
        for rel in changed_files:
            path = rel if os.path.isabs(rel) else os.path.join(project_dir, rel)
            if os.path.exists(path) and path.endswith(".java"):
                results.append(self.parse_file(path))
        return results

    def parse_file(self, file_path):
        with open(file_path, "rb") as f:
            content = f.read()
        tree = self.parser.parse(content)
        return self._extract_metadata(tree.root_node, content, file_path)

    def _extract_metadata(self, root_node, content, file_path):
        metadata = {
            "class": None,
            "className": None,
            "classHash": hashlib.sha256(content).hexdigest(),
            "filePath": file_path,
            "methods": [],
            "dependencies": [],
            "classAnnotations": [],
            "classDoc": None,
        }

        last_comment = None
        for node in root_node.children:
            if node.type in ("block_comment", "line_comment"):
                last_comment = content[node.start_byte : node.end_byte].decode("utf-8")
            if node.type == "class_declaration":
                metadata["classDoc"] = last_comment
                name_node = node.child_by_field_name("name")
                if name_node:
                    metadata["class"] = content[name_node.start_byte : name_node.end_byte].decode("utf-8")
                    metadata["className"] = metadata["class"]
                for child in node.children:
                    if child.type == "modifiers":
                        metadata["classAnnotations"].extend(self._extract_annotations(child, content))
                body = node.child_by_field_name("body")
                if body:
                    last_member_comment = None
                    for member in body.children:
                        if member.type in ("block_comment", "line_comment"):
                            last_member_comment = content[member.start_byte : member.end_byte].decode("utf-8")
                        elif member.type == "method_declaration":
                            method_data = self._parse_method(member, content)
                            method_data["doc"] = last_member_comment
                            cfg = self.cfg_builder.build_cfg(member, content, method_data["id"])
                            method_data["decisions"] = cfg["decisions"]
                            method_data["paths"] = cfg["paths"]
                            method_data["cyclomaticComplexity"] = cfg["cyclomaticComplexity"]
                            method_data["dfg"] = self.dfg_builder.build_dfg(member, content, method_data["decisions"])
                            method_data["calls"] = self._extract_calls(member, content)
                            metadata["methods"].append(method_data)
                            last_member_comment = None
                        else:
                            last_member_comment = None
            if node.type == "import_declaration":
                dep = content[node.start_byte : node.end_byte].decode("utf-8").strip()
                metadata["dependencies"].append(dep.replace("import ", "").replace(";", ""))
                last_comment = None
        return metadata

    def _extract_annotations(self, modifiers_node, content):
        annotations = []
        for child in modifiers_node.children:
            if child.type in ("marker_annotation", "annotation"):
                annotations.append(content[child.start_byte : child.end_byte].decode("utf-8"))
        return annotations

    def _parse_method(self, node, content):
        name_node = node.child_by_field_name("name")
        name = content[name_node.start_byte : name_node.end_byte].decode("utf-8") if name_node else "unknown"
        return_type_node = node.child_by_field_name("type")
        return_type = content[return_type_node.start_byte : return_type_node.end_byte].decode("utf-8") if return_type_node else "void"
        body_node = node.child_by_field_name("body")
        body_content = content[body_node.start_byte : body_node.end_byte] if body_node else b""
        method_hash = hashlib.sha256(body_content).hexdigest()

        parameters = []
        params_node = node.child_by_field_name("parameters")
        if params_node:
            for param in params_node.children:
                if param.type == "formal_parameter":
                    p_type_node = param.child_by_field_name("type")
                    p_name_node = param.child_by_field_name("name")
                    p_type = content[p_type_node.start_byte : p_type_node.end_byte].decode("utf-8") if p_type_node else "unknown"
                    p_name = content[p_name_node.start_byte : p_name_node.end_byte].decode("utf-8") if p_name_node else "unknown"
                    parameters.append({"name": p_name, "type": p_type})

        annotations = []
        throws_types = []
        exception_handlers = []
        for child in node.children:
            if child.type == "modifiers":
                annotations.extend(self._extract_annotations(child, content))
            if child.type == "throws":
                for t in child.children:
                    if t.type in ("type_identifier", "scoped_type_identifier"):
                        throws_types.append(content[t.start_byte : t.end_byte].decode("utf-8"))
        if body_node:
            exception_handlers = self._extract_exception_handlers(body_node, content)

        return {
            "id": f"M_{name}_{method_hash[:8]}",
            "name": name,
            "returnType": return_type,
            "methodHash": method_hash,
            "parameters": parameters,
            "annotations": annotations,
            "throwsTypes": throws_types,
            "exceptionHandlers": exception_handlers,
            "lineStart": node.start_point[0] + 1,
            "lineEnd": node.end_point[0] + 1,
            "status": "DIRTY",
        }

    def _extract_exception_handlers(self, body_node, content):
        handlers = []
        self._walk_handlers(body_node, content, handlers)
        return handlers

    def _walk_handlers(self, node, content, handlers):
        if node.type == "catch_clause":
            param = node.child_by_field_name("parameter")
            catch_type = "Exception"
            if param:
                type_node = param.child_by_field_name("type")
                if type_node:
                    catch_type = content[type_node.start_byte : type_node.end_byte].decode("utf-8")
            body = node.child_by_field_name("body")
            action = "handle"
            if body:
                body_text = content[body.start_byte : body.end_byte].decode("utf-8")
                if "throw " in body_text or "throws " in body_text:
                    action = "throw"
                elif "return " in body_text:
                    action = "return"
                elif "log" in body_text.lower() and "throw " not in body_text and "return " not in body_text:
                    action = "log"
            handlers.append({"catchType": catch_type, "action": action})
        for child in node.children:
            self._walk_handlers(child, content, handlers)

    def _extract_calls(self, method_node, content):
        calls = []
        body = method_node.child_by_field_name("body")
        if body:
            self._walk_calls(body, content, calls)
        return calls

    def _walk_calls(self, node, content, calls):
        if node.type == "method_invocation":
            name_node = node.child_by_field_name("name")
            obj_node = node.child_by_field_name("object")
            if name_node:
                name = content[name_node.start_byte : name_node.end_byte].decode("utf-8")
                external = False
                if obj_node:
                    obj = content[obj_node.start_byte : obj_node.end_byte].decode("utf-8")
                    if obj[0].isupper() and obj not in ("this", "super"):
                        external = True
                        name = f"{obj}.{name}"
                calls.append({"name": name, "line": node.start_point[0] + 1, "external": external})
        for child in node.children:
            self._walk_calls(child, content, calls)


class IncrementalParser:
    def __init__(self, graph_store):
        self.store = graph_store
        self.parser = JavaParser()

    def run(self, project_dir, full_graph):
        prev_manifest = self.store.load_hash_manifest()
        changed_files = self._changed_java_files(project_dir)
        if not changed_files:
            for m in full_graph.get("methods", []):
                prev = prev_manifest.get(m["id"], {})
                if prev.get("methodHash") == m["methodHash"]:
                    m["status"] = "CLEAN"
            return full_graph, []

        reparsed = self.parser.parse_changed_files(project_dir, changed_files)
        reparsed_by_class = {c["class"]: c for c in reparsed}

        for cls in full_graph.get("classes", []):
            if cls["class"] in reparsed_by_class:
                new_cls = reparsed_by_class[cls["class"]]
                new_by_name = {m["name"]: m for m in new_cls["methods"]}
                updated = []
                for old_m in cls["methods"]:
                    nm = new_by_name.get(old_m["name"])
                    if nm:
                        prev_hash = prev_manifest.get(old_m["id"], {}).get("methodHash")
                        if prev_hash != nm["methodHash"]:
                            nm["status"] = "DIRTY"
                        else:
                            nm["status"] = "CLEAN"
                        updated.append(nm)
                    else:
                        old_m["status"] = "DELETED"
                for nm in new_cls["methods"]:
                    if nm["name"] not in {m["name"] for m in cls["methods"]}:
                        nm["status"] = "NEW"
                        updated.append(nm)
                cls["methods"] = updated

        methods = []
        for cls in full_graph["classes"]:
            for m in cls["methods"]:
                m["className"] = cls["class"]
                methods.append(m)
        full_graph["methods"] = methods
        return full_graph, changed_files

    def _changed_java_files(self, project_dir):
        import subprocess
        try:
            parent_ref = self._resolve_parent_commit(project_dir)
            if not parent_ref:
                return []
            diff = subprocess.check_output(
                ["git", "diff", "--name-only", parent_ref, "HEAD"],
                cwd=project_dir, stderr=subprocess.DEVNULL, text=True,
            )
            return [f for f in diff.splitlines() if f.endswith(".java")]
        except (subprocess.CalledProcessError, FileNotFoundError):
            return []

    def _resolve_parent_commit(self, project_dir):
        import subprocess
        try:
            output = subprocess.check_output(
                ["git", "rev-list", "--parents", "HEAD"],
                cwd=project_dir, stderr=subprocess.DEVNULL, text=True,
            )
            lines = [l.strip() for l in output.strip().splitlines() if l.strip()]
            if not lines:
                return None
            commits = lines[0].split()
            if len(commits) >= 2:
                return commits[1]
            return None
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None
