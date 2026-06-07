import tree_sitter_java as tsjava
from tree_sitter import Language, Parser

JAVA_LANGUAGE = Language(tsjava.language())


class CallGraphBuilder:
    def build_call_graph(self, classes, commit=None):
        edges = []
        method_index = {}
        for cls in classes:
            cls_name = cls.get("class", "")
            for method in cls.get("methods", []):
                method_index[method["id"]] = {"class": cls_name, "name": method["name"]}
                for call in method.get("calls", []):
                    target_id = self._resolve_target(call, cls, classes)
                    edge = {
                        "caller": method["id"],
                        "callee": target_id or call.get("name"),
                        "callSite": call.get("line", 0),
                        "type": "STUB_BOUNDARY" if call.get("external") else "DIRECT",
                    }
                    edges.append(edge)

        caller_map = {}
        callee_map = {}
        for e in edges:
            caller_map.setdefault(e["callee"], []).append(e["caller"])
            callee_map.setdefault(e["caller"], []).append(e["callee"])

        result = {
            "graphVersion": "1.3.0",
            "callEdges": edges,
            "callerMap": caller_map,
            "calleeMap": callee_map,
        }
        if commit:
            result["commit"] = commit
        return result

    def _resolve_target(self, call, cls, classes):
        name = call.get("name", "")
        for m in cls.get("methods", []):
            if m["name"] == name:
                return m["id"]
        for other in classes:
            for m in other.get("methods", []):
                if m["name"] == name:
                    return m["id"]
        return None

    def in_degree(self, method_id, call_graph):
        return len(call_graph.get("callerMap", {}).get(method_id, []))


class DFGBuilder:
    def build_dfg(self, method_node, content, decisions=None):
        decisions = decisions or []
        chains_raw = {}
        params = self._extract_parameters(method_node, content)
        for p in params:
            chains_raw[p] = {
                "variable": p,
                "defSite": {"line": method_node.start_point[0] + 1, "nodeId": "PARAM", "source": "PARAMETER"},
                "useSites": [],
                "flowsIntoDecisions": [],
            }

        self._trace(method_node, content, chains_raw, params)
        def_use_chains = list(chains_raw.values())

        decision_input_map = {}
        for d in decisions:
            vars_in_cond = set()
            origins = []
            for cond in d.get("conditions", []):
                for var in chains_raw:
                    if var in cond:
                        vars_in_cond.add(var)
                        origins.append(chains_raw[var]["defSite"]["source"])
            decision_input_map[d["decisionId"]] = {
                "conditions": d.get("conditions", []),
                "sourceVariables": list(vars_in_cond) or [c.split()[0] for c in d.get("conditions", []) if c],
                "sourceOrigin": origins or ["PARAMETER"] * len(d.get("conditions", [])),
            }

        return {
            "defUseChains": def_use_chains,
            "decisionInputMap": decision_input_map,
        }

    def _extract_parameters(self, method_node, content):
        params = []
        params_node = method_node.child_by_field_name("parameters")
        if not params_node:
            return params
        for param in params_node.children:
            if param.type == "formal_parameter":
                name_node = param.child_by_field_name("name")
                if name_node:
                    params.append(content[name_node.start_byte:name_node.end_byte].decode("utf-8"))
        return params

    def _trace(self, node, content, chains_raw, params):
        if node.type == "variable_declarator":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = content[name_node.start_byte:name_node.end_byte].decode("utf-8")
                if name not in chains_raw:
                    chains_raw[name] = {
                        "variable": name,
                        "defSite": {"line": node.start_point[0] + 1, "nodeId": f"N_{name}", "source": "COMPUTED"},
                        "useSites": [],
                        "flowsIntoDecisions": [],
                    }

        if node.type == "identifier":
            parent = node.parent
            if parent and parent.type not in ("variable_declarator", "method_declaration", "formal_parameter"):
                name = content[node.start_byte:node.end_byte].decode("utf-8")
                if name in chains_raw:
                    use = {"line": node.start_point[0] + 1, "nodeId": f"U_{node.start_point[0]}", "role": "USAGE"}
                    if parent.type in ("if_statement", "while_statement", "for_statement"):
                        use["role"] = "CONDITION_INPUT"
                        for d_id in chains_raw[name].setdefault("flowsIntoDecisions", []):
                            pass
                    chains_raw[name]["useSites"].append(use)
                elif name in params or name[0].islower():
                    chains_raw[name] = {
                        "variable": name,
                        "defSite": {"line": 0, "nodeId": "PARAM", "source": "PARAMETER"},
                        "useSites": [{"line": node.start_point[0] + 1, "nodeId": "U", "role": "USAGE"}],
                        "flowsIntoDecisions": [],
                    }

        for child in node.children:
            self._trace(child, content, chains_raw, params)
