import tree_sitter_java as tsjava
from tree_sitter import Language, Parser

JAVA_LANGUAGE = Language(tsjava.language())


class CFGBuilder:
    def __init__(self):
        self.parser = Parser(JAVA_LANGUAGE)
        self._node_counter = 0

    def build_cfg(self, method_node, content, method_id):
        body = method_node.child_by_field_name("body")
        if not body:
            return {"methodId": method_id, "decisions": [], "paths": [], "cyclomaticComplexity": 1}

        decisions = []
        self._find_decisions(body, content, decisions)
        paths = self._build_paths(decisions)
        return {
            "methodId": method_id,
            "decisions": decisions,
            "paths": paths,
            "cyclomaticComplexity": 1 + len(decisions),
        }

    def _find_decisions(self, node, content, decisions):
        if node.type in ("if_statement", "while_statement", "for_statement"):
            condition_node = node.child_by_field_name("condition")
            if condition_node:
                expr = content[condition_node.start_byte : condition_node.end_byte].decode("utf-8")
                if expr.startswith("(") and expr.endswith(")"):
                    expr = expr[1:-1]
                conditions = self._extract_atomic_conditions(condition_node, content)
                d_id = f"D_{node.start_point[0]}_{node.start_point[1]}"
                t_node = f"N_{self._next_node()}"
                f_node = f"N_{self._next_node()}"
                decisions.append({
                    "decisionId": d_id,
                    "line": node.start_point[0] + 1,
                    "expression": expr,
                    "conditions": conditions,
                    "conditionCount": len(conditions),
                    "requiredMcdcPairs": len(conditions),
                    "branches": [
                        {"id": f"B_{node.start_point[0]}_T", "outcome": "true", "targetNode": t_node},
                        {"id": f"B_{node.start_point[0]}_F", "outcome": "false", "targetNode": f_node},
                    ],
                })
        for child in node.children:
            self._find_decisions(child, content, decisions)

    def _build_paths(self, decisions):
        paths = []
        for i, d in enumerate(decisions):
            d_id = d["decisionId"]
            paths.append({"pathId": f"P_{i}_T", "nodes": ["N010", f"{d_id}-true", d["branches"][0]["targetNode"], "N025"]})
            paths.append({"pathId": f"P_{i}_F", "nodes": ["N010", f"{d_id}-false", d["branches"][1]["targetNode"], "N025"]})
        return paths

    def _next_node(self):
        self._node_counter += 1
        return self._node_counter

    def _extract_atomic_conditions(self, node, content):
        conditions = []
        self._collect_atomic(node, content, conditions)
        return list(dict.fromkeys(conditions))

    def _collect_atomic(self, node, content, conditions):
        if node.type == "binary_expression":
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            operator_node = node.children[1] if len(node.children) > 1 else None
            operator = content[operator_node.start_byte : operator_node.end_byte].decode("utf-8") if operator_node else ""
            if operator in ("&&", "||"):
                self._collect_atomic(left, content, conditions)
                self._collect_atomic(right, content, conditions)
            else:
                conditions.append(content[node.start_byte : node.end_byte].decode("utf-8"))
        elif node.type == "parenthesized_expression" and len(node.children) > 1:
            self._collect_atomic(node.children[1], content, conditions)
        else:
            conditions.append(content[node.start_byte : node.end_byte].decode("utf-8"))
