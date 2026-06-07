def build_graph_slice(method, class_name, call_graph=None, memory_facts=None):
    memory_facts = memory_facts or {}
    slice_data = {
        "methodId": method["id"],
        "methodName": method["name"],
        "className": class_name,
        "returnType": method.get("returnType"),
        "parameters": method.get("parameters", []),
        "annotations": method.get("annotations", []),
        "throwsTypes": method.get("throwsTypes", []),
        "semantic": method.get("semantic", {}),
        "decisions": method.get("decisions", []),
        "dfg": method.get("dfg", {}),
        "coverage": method.get("coverage", {}),
        "mutation": method.get("mutation", {}),
        "risk": method.get("risk", {}),
        "agentMemorySlice": memory_facts,
    }
    if call_graph:
        callers = call_graph.get("callerMap", {}).get(method["id"], [])
        callees = call_graph.get("calleeMap", {}).get(method["id"], [])
        slice_data["callChain"] = {"callers": callers, "callees": callees}
    return slice_data
