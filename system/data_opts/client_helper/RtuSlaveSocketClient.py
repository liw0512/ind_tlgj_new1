import csv
import json
import logging
import socket
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path


class RtuSlaveSocketClient:
    """Socket client for the rtu_slave JSON bridge.

    The bridge in ``rtu_slave/modbus_rtu_slave.py`` exposes newline-delimited
    JSON commands:

    - {"cmd": "read"}
    - {"cmd": "write", "data": {...}}

    This adapter keeps the same operational shape as ``ModbusTCPClient`` so it
    can be started by the existing data collection threads.
    """

    PLC_TO_STD_KEYS = {
        "AFT塔PH": ("aptjy_PH", "aptjy_PH1"),
        "AFT塔液位": ("apt_YW",),
        "吸收塔密度": ("xstshsjy_MD",),
        "吸收塔PH": ("xstjy_PH", "xstjy_PH1"),
        "吸收塔液位": ("xst_YW",),
        "吸收塔出口烟气SO2": ("jyq_SO2",),
        "吸收塔入口烟气SO2": ("yyq_SO2",),
        "锅炉负荷": ("jzfh",),
        "塔外浆液池B浆液泵电流": ("xstjyxhb_DDL",),
        "塔外浆液池A浆液泵电流": ("xstjyxhb_CDL",),
        "吸收塔循环泵B电流": ("xstjyxhb_BDL",),
        "吸收塔循环泵A电流": ("xstjyxhb_ADL",),
        "塔外液箱石膏浆液密度": ("twshsjy_MD",),
        "塔外液箱石膏浆液PH": ("twjy_PH",),
        "塔外浆液箱液位": ("twjy_YW",),
        "AFT塔浆液密度": ("aptshsjy_MD",),
        "心跳": ("jym",),
        "AFT塔石灰石浆液流量": ("aptshsjy_LL",),
        "吸收塔石灰石浆液流量": ("xstshsjy_LL",),
        "吸收塔入口烟气O2": ("yyq_O2",),
        "吸收塔入口烟气流量": ("yyq_LL",),
        "AFT塔循环泵C电流": ("aptjyxhb_CDL",),
        "AFT塔循环泵B电流": ("aptjyxhb_BDL",),
        "AFT塔循环泵A电流": ("aptjyxhb_ADL",),
    }
    HARDCODED_PLC_POINT_NAMES = tuple(PLC_TO_STD_KEYS.keys())
    PUMP_COMMAND_POINT_NAMES = (
        "吸收塔A循环泵",
        "吸收塔B循环泵",
        "塔外浆池A浆液循环泵",
        "塔外浆池B浆液循环泵",
        "AFT塔A循环泵",
        "AFT塔B循环泵",
        "AFT塔C循环泵",
    )

    HEARTBEAT_CANDIDATES = ("心跳", "heartbeat", "Heartbeat", "备用1")
    def __init__(
        self,
        host=None,
        port=None,
        global_data=None,
        config_dir=None,
        timeout=5,
        poll_interval=1,
        write_interval=30,
        reconnect_interval=5,
    ):
        if isinstance(host, dict) and global_data is None:
            global_data = host
            host = None

        self.global_data = global_data if global_data is not None else {}
        self.global_data.setdefault("data", [])
        self.global_data["connection_status"] = False

        self.config_dir = Path(config_dir) if config_dir else self._default_config_dir()
        self.config = self._load_json_config()
        socket_cfg = self.config.get("socket", {})

        cfg_host = socket_cfg.get("host", "127.0.0.1")
        self.host = host or ("127.0.0.1" if cfg_host in ("0.0.0.0", "::") else cfg_host)
        self.port = int(port or socket_cfg.get("port", 9000))
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.write_interval = write_interval
        self.reconnect_interval = reconnect_interval

        self.client = None
        self.lock = threading.Lock()
        self.running = False
        self.previous_values = {}

        self.plc_point_names = list(self.HARDCODED_PLC_POINT_NAMES)
        self.write_point_configs = self._load_point_configs("me2plc.csv")
        self.write_point_names = [point["name"] for point in self.write_point_configs]
        if not self.write_point_names:
            self.write_point_names = list(self.PUMP_COMMAND_POINT_NAMES)
        self.pump_command_point_names = list(self.PUMP_COMMAND_POINT_NAMES)
        self.heartbeat_point = self._find_heartbeat_point()

    @staticmethod
    def _default_config_dir():
        current = Path(__file__).resolve()
        server_config = Path("/root/rtu_slave/config")
        if server_config.exists():
            return server_config
        local_config = current.parent / "rtu_slave" / "config"
        if local_config.exists():
            return local_config
        repo_root = current.parents[3]
        root_config = repo_root / "rtu_slave" / "config"
        if root_config.exists():
            return root_config
        return local_config

    def _load_json_config(self):
        config_path = self.config_dir / "config.json"
        if not config_path.exists():
            return {"socket": {"host": "127.0.0.1", "port": 9000}}
        with config_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _load_point_names(self, filename):
        return [point["name"] for point in self._load_point_configs(filename)]

    def _load_point_configs(self, filename):
        path = self.config_dir / filename
        if not path.exists():
            logging.warning("点位文件不存在: %s", path)
            return []

        points = []
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = (row.get("name") or "").strip()
                if name and not name.startswith("#"):
                    points.append({
                        "name": name,
                        "address": (row.get("address") or "").strip(),
                        "type": (row.get("type") or "").strip(),
                        "bit": (row.get("bit") or "").strip(),
                    })
        return points

    def _find_heartbeat_point(self):
        for name in self.HEARTBEAT_CANDIDATES:
            if name in self.write_point_names:
                return name
        return None

    def connect(self):
        with self.lock:
            return self._connect_unlocked()

    def _connect_unlocked(self):
        self._close_unlocked()
        try:
            self.client = socket.create_connection((self.host, self.port), timeout=self.timeout)
            self.client.settimeout(self.timeout)
            self.global_data["connection_status"] = True
            logging.info("已连接 RTU slave socket: %s:%s", self.host, self.port)
            return True
        except Exception as e:
            self.client = None
            self.global_data["connection_status"] = False
            logging.error("连接 RTU slave socket 失败 %s:%s: %s", self.host, self.port, e)
            return False

    def start(self):
        return self.connect()

    def stop(self):
        self.running = False
        with self.lock:
            self._close_unlocked()
        self.global_data["connection_status"] = False

    def _close_unlocked(self):
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None

    def _recv_line_unlocked(self):
        chunks = []
        while True:
            chunk = self.client.recv(4096)
            if not chunk:
                raise ConnectionError("socket closed")
            chunks.append(chunk)
            joined = b"".join(chunks)
            if b"\n" in joined:
                line, _ = joined.split(b"\n", 1)
                return line.decode("utf-8")

    def _request(self, message):
        with self.lock:
            if self.client is None and not self._connect_unlocked():
                raise ConnectionError("rtu_slave socket is not connected")

            try:
                payload = json.dumps(message, ensure_ascii=False) + "\n"
                self.client.sendall(payload.encode("utf-8"))
                response = json.loads(self._recv_line_unlocked())
                self.global_data["connection_status"] = True
                return response
            except Exception:
                self._close_unlocked()
                self.global_data["connection_status"] = False
                raise

    def read_dcs(self):
        response = self._request({"cmd": "read"})
        if response.get("cmd") != "read_resp":
            raise ValueError(f"invalid read response: {response}")
        self.global_data["connection_status"] = bool(response.get("connected", True))
        return response.get("data") or {}

    @staticmethod
    def _to_number(value):
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return value
        if value is None:
            return None
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return value

    def safe_get(self, dictionary, key, default=0):
        value = self._to_number(dictionary.get(key))

        if value is None:
            self.previous_values.pop(key, None)
            return default
        if isinstance(value, (int, float)) and value < 0:
            self.previous_values.pop(key, None)
            return default

        self.previous_values[key] = value
        return value

    def _build_std_data(self, raw_data):
        std_data = {
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "rtu_slave_socket",
        }

        plc_read_points = set(self.plc_point_names)
        for point_name, value in raw_data.items():
            if point_name not in plc_read_points:
                continue

            std_data[point_name] = self._to_number(value)
            for std_key in self.PLC_TO_STD_KEYS.get(point_name, ()):
                clean_value = self.safe_get(raw_data, point_name)
                if isinstance(clean_value, (int, float)):
                    clean_value = round(clean_value, 2)
                std_data[std_key] = clean_value

        self._complete_basic_fields(std_data)
        return std_data

    def _complete_basic_fields(self, std_data):
        if "xstjy_PH" not in std_data and "xstjy_PH1" in std_data:
            std_data["xstjy_PH"] = std_data["xstjy_PH1"]
        if "aptjy_PH" not in std_data and "aptjy_PH1" in std_data:
            std_data["aptjy_PH"] = std_data["aptjy_PH1"]

    def run(self):
        self.running = True
        while self.running:
            try:
                raw_data = self.read_dcs()
                std_data = self._build_std_data(raw_data)
                self.global_data["data"].append(std_data)
                logging.info("获取到 rtu_slave 数据: %s", std_data)
                time.sleep(self.poll_interval)
            except Exception as e:
                logging.error("读取 rtu_slave 数据失败: %s", e)
                traceback.print_exc()
                time.sleep(self.reconnect_interval)

    @staticmethod
    def _parse_pump_status(status):
        if status is None:
            return None
        if isinstance(status, (list, tuple)):
            values = list(status)
        else:
            text = str(status).strip()
            if not text:
                return None
            values = text.split("-") if "-" in text else list(text)

        result = []
        for value in values:
            try:
                # recommended_pump may contain gear levels. PLC command points
                # are bit commands here, so every level greater than 1 is sent
                # as 1, while 0 stays 0.
                level = int(float(value))
                result.append(1 if level >= 1 else 0)
            except (TypeError, ValueError):
                result.append(1 if str(value).lower() in ("true", "on", "yes") else 0)
        return result

    def _build_write_payload(self, map_control_result):
        payload = {}

        status = map_control_result.get("recommended_pump")
        pump_values = self._parse_pump_status(status)
        if pump_values:
            for name, value in zip(self.pump_command_point_names, pump_values):
                if name in self.write_point_names:
                    payload[name] = bool(value)

        return payload

    def write_points(self, data):
        if not data:
            return {}
        response = self._request({"cmd": "write", "data": data})
        if response.get("cmd") != "write_resp":
            raise ValueError(f"invalid write response: {response}")
        return response.get("results") or {}

    def send_heart(self):
        if self.heartbeat_point is None:
            logging.warning("me2plc.csv 中未配置心跳点位，send_heart 将不写入数据")
            return

        heart_value = 1
        while True:
            try:
                result = self.write_points({self.heartbeat_point: heart_value})
                logging.info("心跳写入 %s=%s: %s", self.heartbeat_point, heart_value, result)
                heart_value = 2 if heart_value == 1 else 1
            except Exception as e:
                logging.error("心跳写入失败: %s", e)
                traceback.print_exc()
                time.sleep(self.reconnect_interval)
            finally:
                time.sleep(5)

    def send_cnn_to_dcs(self):
        while True:
            try:
                map_control_result = self.global_data.get("map_control")
                if not map_control_result:
                    continue

                payload = self._build_write_payload(map_control_result)
                if payload:
                    result = self.write_points(payload)
                    logging.info("写入 rtu_slave me2plc 数据: payload=%s result=%s", payload, result)
                else:
                    logging.debug("map_control 中没有匹配 me2plc.csv 的写入点")
            except Exception as e:
                logging.error("写入 rtu_slave 数据失败: %s", e)
                traceback.print_exc()
                time.sleep(self.reconnect_interval)
            finally:
                time.sleep(self.write_interval)


RTUSlaveSocketClient = RtuSlaveSocketClient
ModbusRtuSocketClient = RtuSlaveSocketClient
