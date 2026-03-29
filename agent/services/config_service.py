import json
import logging

def unwrap_config(data):
    """
    Entpackt Konfigurationswerte, die ggf. in einem Envelope stecken.
    (Rekursive Hilfsfunktion fuer Config-API und DB-Migrationen)
    """
    if not isinstance(data, dict):
        return data

    if "value" in data and len(data) == 1:
        return unwrap_config(data["value"])

    unwrapped = {}
    for k, v in data.items():
        if isinstance(v, dict):
            if "value" in v and len(v) == 1:
                unwrapped[k] = unwrap_config(v["value"])
            elif "nested" in v and len(v) == 1:
                nested = v["nested"]
                unwrapped[k] = unwrap_config(nested)
            else:
                unwrapped[k] = {k2: unwrap_config(v2) for k2, v2 in v.items()}
        elif isinstance(v, list):
            unwrapped[k] = [unwrap_config(i) for i in v]
        else:
            unwrapped[k] = v
    return unwrapped
