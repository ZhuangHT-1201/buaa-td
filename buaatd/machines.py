MACHINES = [
    {"id": 2, "machinesn": "20211025001", "location": "北航本部TD入口1", "doortype": "1"},
    {"id": 3, "machinesn": "20220301004", "location": "北航本部TD入口3", "doortype": "1"},
    {"id": 4, "machinesn": "20230417001", "location": "北航本部TD入口2", "doortype": "1"},
    {"id": 5, "machinesn": "20210421003", "location": "北航本部TD出口2", "doortype": "2"},
    {"id": 6, "machinesn": "20210420002", "location": "北航本部TD出口1", "doortype": "2"},
    {"id": 7, "machinesn": "20220301003", "location": "北航本部TD出口3", "doortype": "2"},
    {"id": 8, "machinesn": "20210511001", "location": "北航沙河TD入口1", "doortype": "1"},
    {"id": 9, "machinesn": "20210511002", "location": "北航沙河TD入口2", "doortype": "1"},
    {"id": 10, "machinesn": "20210511003", "location": "北航沙河TD入口3", "doortype": "1"},
    {"id": 11, "machinesn": "20220218001", "location": "北航沙河TD出口1", "doortype": "2"},
    {"id": 12, "machinesn": "20220218002", "location": "北航沙河TD出口2", "doortype": "2"},
    {"id": 13, "machinesn": "20220218003", "location": "北航沙河TD出口3", "doortype": "2"},
]

CAMPUS_ALIASES = {
    "1": "xueyuanlu",
    "学院路": "xueyuanlu",
    "學院路": "xueyuanlu",
    "本部": "xueyuanlu",
    "xyl": "xueyuanlu",
    "xueyuanlu": "xueyuanlu",
    "2": "shahe",
    "沙河": "shahe",
    "sh": "shahe",
    "shahe": "shahe",
}


def normalize_campus(campus: str) -> str:
    key = campus.strip().lower()
    if key not in CAMPUS_ALIASES:
        raise ValueError("校区只支持 学院路 / 沙河")
    return CAMPUS_ALIASES[key]


def campus_label(campus: str) -> str:
    return "沙河" if normalize_campus(campus) == "shahe" else "学院路"


def machine_pool(campus: str, entrance: bool):
    key = normalize_campus(campus)
    result = []
    for machine in MACHINES:
        location = machine["location"]
        if key == "shahe":
            if "沙河" not in location:
                continue
        elif "学院路" not in location and "本部" not in location:
            continue
        is_entrance = machine["doortype"] == "1" or "入口" in location
        if entrance and is_entrance:
            result.append(machine)
        elif not entrance and not is_entrance:
            result.append(machine)
    return result


def default_config():
    from .const import CHECK_TYPE, EVENTNO, SCHOOLNO, SERVER_IP, SERVER_PORT, DEFAULT_SOCKET_TIMEOUT

    return {
        "type": CHECK_TYPE,
        "schoolno": SCHOOLNO,
        "eventno": EVENTNO,
        "server": {"ip": SERVER_IP, "port": SERVER_PORT, "timeout": DEFAULT_SOCKET_TIMEOUT},
        "machine": list(MACHINES),
    }
