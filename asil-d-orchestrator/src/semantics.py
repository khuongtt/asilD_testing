import re


class SemanticAnnotator:
    def __init__(self):
        self.domain_keywords = {
            "BrakeSystem": ["brake", "pressure", "abs", "stopping"],
            "PowerTrain": ["engine", "transmission", "torque", "speed"],
            "VehicleIdentification": ["vin", "ecu", "identification"],
        }
        self.sub_domains = {
            "BrakeSystem": "PressureControl",
            "PowerTrain": "EngineControl",
            "VehicleIdentification": "VINValidation",
        }

    def annotate(self, method_data, class_data, requirements=None):
        doc = method_data.get("doc") or class_data.get("classDoc") or ""
        annotations = [a.lower() for a in (method_data.get("annotations", []) + class_data.get("classAnnotations", []))]

        asil_level, asil_source = self._extract_asil(doc, annotations)
        safety_critical = self._is_safety_critical(doc, annotations)
        domain = self._detect_domain(method_data["name"], class_data["class"], doc)
        linked = self._link_requirements(method_data["name"], domain, requirements or [])

        confidence = 1.0 if asil_source == "EXPLICIT" else (0.72 if asil_level != "QM" else 0.5)
        return {
            "safetyCritical": safety_critical,
            "asilLevel": asil_level,
            "domain": domain,
            "subDomain": self.sub_domains.get(domain, "Formatting"),
            "functionalDescription": self._extract_description(doc),
            "safeState": self._extract_safe_state(doc),
            "linkedRequirements": linked,
            "annotationSource": asil_source,
            "confidence": confidence,
        }

    def _extract_asil(self, doc, annotations):
        if any("asild" in a for a in annotations) or "@asild" in doc.lower():
            return "D", "EXPLICIT"
        if any("asilc" in a for a in annotations) or "@asilc" in doc.lower():
            return "C", "EXPLICIT"
        if any("asilb" in a for a in annotations) or "@asilb" in doc.lower():
            return "B", "EXPLICIT"
        if any("asila" in a for a in annotations) or "@asila" in doc.lower():
            return "A", "EXPLICIT"
        if any(k in doc.lower() for k in ["brake", "safety", "critical"]):
            return "D", "HEURISTIC"
        return "QM", "HEURISTIC"

    def _is_safety_critical(self, doc, annotations):
        if any("safetycritical" in a for a in annotations):
            return True
        return "@safetycritical" in doc.lower()

    def _detect_domain(self, method_name, class_name, doc):
        text = f"{method_name} {class_name} {doc}".lower()
        for domain, keywords in self.domain_keywords.items():
            if any(k in text for k in keywords):
                return domain
        return "Utility"

    def _extract_description(self, doc):
        if not doc:
            return None
        for line in doc.splitlines():
            clean = line.strip().replace("/**", "").replace("*/", "").replace("*", "").strip()
            if clean and not clean.startswith("@"):
                return clean
        return None

    def _extract_safe_state(self, doc):
        if not doc:
            return None
        match = re.search(r"@safeState\s+(.+)", doc, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return "RETURN_ZERO_PRESSURE" if "brake" in doc.lower() else None

    def _link_requirements(self, method_name, domain, requirements):
        linked = []
        for req in requirements:
            desc = req.get("description", "").lower()
            if method_name.lower() in desc or domain.lower().replace("system", "") in desc:
                linked.append(req["requirementId"])
            elif domain == "BrakeSystem" and "brake" in desc:
                linked.append(req["requirementId"])
        return linked


def parse_requirements_file(path):
    requirements = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 3:
                    requirements.append({
                        "requirementId": parts[0],
                        "asilLevel": parts[1].replace("ASIL-", ""),
                        "description": parts[2],
                        "mappedMethods": [],
                        "verificationStatus": "PARTIAL",
                        "openGaps": [],
                    })
    except OSError:
        pass
    return requirements
