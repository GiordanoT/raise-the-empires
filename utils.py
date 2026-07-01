def simple_list(raw_list):
    if not raw_list and raw_list != 0:
        return []
    return raw_list if isinstance(raw_list, list) else [raw_list]
