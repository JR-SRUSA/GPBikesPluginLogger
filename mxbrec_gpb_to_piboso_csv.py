#!/usr/bin/env python3
"""
mxbrec_gpb_to_piboso_csv.py

Converts .mxbrec files written by gpb_binary_recorder.c into a PiBoSo-style CSV:

"Format","PiBoSo CSV File"
"Venue","..."
...

"Time","Distance",...
"s","m",...

"0.000","0.000",...

Notes:
- Uses EventInit metadata for Venue / Vehicle / User when available.
- Uses RunTelemetry samples for the data rows.
- Includes the standard PiBoSo-like columns first, then additional recorded fields.
- Distance = run_pos * track_length when track_length is available; otherwise run_pos.
"""

import csv
import math
import os
import statistics
import struct
import sys
from datetime import datetime, timezone

EVENT_NAMES = {
    0: "None", 1: "Startup", 2: "Shutdown", 3: "EventInit", 4: "EventDeinit",
    5: "RunInit", 6: "RunDeinit", 7: "RunStart", 8: "RunStop", 9: "RunLap",
    10: "RunSplit", 11: "RunTelemetry", 12: "DrawInit", 13: "Draw",
    14: "TrackCenterline", 15: "RaceEvent", 16: "RaceDeinit", 17: "RaceSession",
    18: "RaceSessionState", 19: "RaceAddEntry", 20: "RaceRemoveEntry", 21: "RaceLap",
    22: "RaceSplit", 23: "RaceHoleshot", 24: "RaceClassification", 25: "RaceTrackPosition",
    26: "RaceCommunication", 27: "RaceVehicleData",
}

HEADER_FMT = "<8sIIQQI32s4x"   # MSVC RecordingHeader size = 72 bytes
EVENT_HEADER_FMT = "<IIQ"
BIKE_DATA_SIZE = 256
G = 9.80665
RAD_TO_DEG = 180.0 / math.pi


def f32(buf, off): return struct.unpack_from("<f", buf, off)[0], off + 4
def i32(buf, off): return struct.unpack_from("<i", buf, off)[0], off + 4

def f32s(buf, off, n):
    vals = list(struct.unpack_from("<" + "f" * n, buf, off))
    return vals, off + 4 * n

def i32s(buf, off, n):
    vals = list(struct.unpack_from("<" + "i" * n, buf, off))
    return vals, off + 4 * n

def cstr(raw):
    return raw.split(b"\0", 1)[0].decode("utf-8", errors="replace")


def decode_event_init(buf):
    o = 0
    out = {}
    out["rider_name"] = cstr(buf[o:o+100]); o += 100
    out["bike_id"] = cstr(buf[o:o+100]); o += 100
    out["bike_name"] = cstr(buf[o:o+100]); o += 100
    out["number_of_gears"], o = i32(buf, o)
    out["max_rpm"], o = i32(buf, o)
    out["limiter"], o = i32(buf, o)
    out["shift_rpm"], o = i32(buf, o)
    out["engine_opt_temperature"], o = f32(buf, o)
    alarms, o = f32s(buf, o, 2)
    out["engine_temperature_alarm_low"] = alarms[0]
    out["engine_temperature_alarm_high"] = alarms[1]
    out["max_fuel"], o = f32(buf, o)
    susp, o = f32s(buf, o, 2)
    out["front_susp_max_travel"] = susp[0]
    out["rear_susp_max_travel"] = susp[1]
    out["steer_lock"] , o = f32(buf, o)
    out["category"] = cstr(buf[o:o+100]); o += 100
    out["track_id"] = cstr(buf[o:o+100]); o += 100
    out["track_name"] = cstr(buf[o:o+100]); o += 100
    out["track_length"], o = f32(buf, o)
    out["type"], o = i32(buf, o)
    return out


def decode_session(buf):
    o = 0
    out = {}
    out["session"], o = i32(buf, o)
    out["conditions"], o = i32(buf, o)
    out["air_temperature"], o = f32(buf, o)
    out["track_temperature"], o = f32(buf, o)
    out["setup_file_name"] = cstr(buf[o:o+100]); o += 100
    return out


def decode_bike_data(buf):
    o = 0
    d = {}
    d["Engine"], o = i32(buf, o)
    d["CylHeadTemp"], o = f32(buf, o)
    d["WaterTemp"], o = f32(buf, o)
    d["Gear"], o = i32(buf, o)
    d["Fuel"], o = f32(buf, o)
    d["Speed"], o = f32(buf, o)
    d["PosX"], o = f32(buf, o); d["PosY_3D"], o = f32(buf, o); d["PosY"], o = f32(buf, o)
    d["VelocityX"], o = f32(buf, o); d["VelocityY"], o = f32(buf, o); d["VelocityZ"], o = f32(buf, o)
    d["AccelerationX"], o = f32(buf, o); d["AccelerationY"], o = f32(buf, o); d["AccelerationZ"], o = f32(buf, o)
    rot, o = f32s(buf, o, 9)
    for r in range(3):
        for c in range(3):
            d[f"Rot{r}{c}"] = rot[r*3+c]
    d["Yaw"], o = f32(buf, o); d["Pitch"], o = f32(buf, o); d["Roll"], o = f32(buf, o)
    d["YawVelRad"], o = f32(buf, o); d["PitchVelRad"], o = f32(buf, o); d["RollVelRad"], o = f32(buf, o)
    d["YawVel"] = d["YawVelRad"] * RAD_TO_DEG
    d["PitchVel"] = d["PitchVelRad"] * RAD_TO_DEG
    d["RollVel"] = d["RollVelRad"] * RAD_TO_DEG
    d["PitchRel"], o = f32(buf, o); d["RollRel"], o = f32(buf, o)
    susp_len, o = f32s(buf, o, 2)
    d["FrontSuspLength"] = susp_len[0]; d["RearSuspLength"] = susp_len[1]
    susp_vel, o = f32s(buf, o, 2)
    d["FrontSuspVelocity"] = susp_vel[0]; d["RearSuspVelocity"] = susp_vel[1]
    d["Crashed"], o = i32(buf, o)
    d["SteerRaw"], o = f32(buf, o)
    d["InputThrottle"], o = f32(buf, o)
    d["Throttle"], o = f32(buf, o)
    d["FrontBrake"] , o = f32(buf, o)
    d["RearBrake"], o = f32(buf, o)
    d["Clutch"], o = f32(buf, o)
    wheel_speed, o = f32s(buf, o, 2)
    d["FrontWheel"] = wheel_speed[0]; d["RearWheel"] = wheel_speed[1]
    mats, o = i32s(buf, o, 2)
    d["FrontWheelMaterial"] = mats[0]; d["RearWheelMaterial"] = mats[1]
    tread, o = f32s(buf, o, 6)
    d["FrontTreadTempLeft"] = tread[0]; d["FrontTreadTempCenter"] = tread[1]; d["FrontTreadTempRight"] = tread[2]
    d["RearTreadTempLeft"] = tread[3]; d["RearTreadTempCenter"] = tread[4]; d["RearTreadTempRight"] = tread[5]
    brake_pressure, o = f32s(buf, o, 2)
    d["FrontBrakePressure"] = brake_pressure[0]; d["RearBrakePressure"] = brake_pressure[1]
    d["SteerTorque"], o = f32(buf, o)
    d["PitLimiter"], o = i32(buf, o)
    d["ECUMode"], o = i32(buf, o)
    d["EngineMapping"] = cstr(buf[o:o+3]); o += 3
    o += 1  # MSVC padding before next int
    d["TractionControl"], o = i32(buf, o)
    d["EngineBraking"], o = i32(buf, o)
    d["AntiWheeling"], o = i32(buf, o)
    d["ECUState"], o = i32(buf, o)
    d["RiderLRLean"], o = f32(buf, o)

    # PiBoSo-ish derived channels.
    # Steering from GP Bikes is usually normalized by steer lock to degrees when event metadata has steer_lock.
    d["LatAcc"] = d["AccelerationX"] / G
    d["LonAcc"] = d["AccelerationZ"] / G
    return d


def fmt_value(v):
    if v is None:
        return ""
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return ""
        return f"{v:.6f}"
    return str(v)


def estimate_sample_rate(rows):
    if len(rows) < 2:
        return ""
    times = [r["Time"] for r in rows]
    diffs = [b - a for a, b in zip(times, times[1:]) if b > a]
    if not diffs:
        return ""
    med = statistics.median(diffs)
    if med <= 0:
        return ""
    hz = round(1.0 / med)
    return str(int(hz)) if hz > 0 else ""


def read_recording(path):
    header_size = struct.calcsize(HEADER_FMT)
    event_header_size = struct.calcsize(EVENT_HEADER_FMT)
    telemetry_rows = []
    events = []
    meta = {}
    session = {}

    with open(path, "rb") as f:
        raw = f.read(header_size)
        if len(raw) != header_size:
            raise RuntimeError("File is too small for RecordingHeader")
        magic, version, num_events, start_us, end_us, flags, _ = struct.unpack(HEADER_FMT, raw)
        if magic != b"MXBHREC\x00":
            raise RuntimeError(f"Bad magic: {magic!r}")

        header = {
            "version": version,
            "num_events": num_events,
            "start_us": start_us,
            "end_us": end_us,
            "flags": flags,
        }

        for idx in range(num_events):
            eh = f.read(event_header_size)
            if len(eh) != event_header_size:
                break
            et, size, ts = struct.unpack(EVENT_HEADER_FMT, eh)
            payload = f.read(size)
            if len(payload) != size:
                break
            events.append((idx, et, size, ts))

            if et == 3 and size >= 624:
                meta.update(decode_event_init(payload))
            elif et == 5 and size >= 116:
                session.update(decode_session(payload))
            elif et == 11 and size >= BIKE_DATA_SIZE + 8:
                bike = payload[:BIKE_DATA_SIZE]
                run_time, run_pos = struct.unpack_from("<ff", payload, size - 8)
                row = decode_bike_data(bike)
                row["Time"] = run_time
                row["RunPos"] = run_pos
                row["TimestampUs"] = ts
                row["EventIndex"] = idx
                telemetry_rows.append(row)

    # Derived fields that need metadata.
    track_length = float(meta.get("track_length") or 0.0)
    steer_lock = float(meta.get("steer_lock") or 0.0)
    front_susp_max = float(meta.get("front_susp_max_travel") or 0.0)
    rear_susp_max = float(meta.get("rear_susp_max_travel") or 0.0)

    for row in telemetry_rows:
        row["Distance"] = row["RunPos"] * track_length if track_length > 0 else row["RunPos"]
        row["Steer"] = row["SteerRaw"] * steer_lock if steer_lock > 0 else row["SteerRaw"]
        row["FrontSusp"] = (row["FrontSuspLength"] / front_susp_max * 100.0) if front_susp_max > 0 else row["FrontSuspLength"]
        row["RearSusp"] = (row["RearSuspLength"] / rear_susp_max * 100.0) if rear_susp_max > 0 else row["RearSuspLength"]

    return header, meta, session, events, telemetry_rows


def write_piboso_csv(input_path, output_path=None):
    header, meta, session, events, rows = read_recording(input_path)
    if output_path is None:
        base, _ = os.path.splitext(input_path)
        output_path = base + "_piboso.csv"

    start_dt = datetime.fromtimestamp(header["start_us"] / 1_000_000, tz=timezone.utc).astimezone()
    duration = max(0.0, (header["end_us"] - header["start_us"]) / 1_000_000.0)
    sample_rate = estimate_sample_rate(rows)

    # Standard PiBoSo-like channels first, then additional recorded fields.
    columns = [
        "Time", "Distance", "Engine", "CylHeadTemp", "WaterTemp", "Gear", "Speed",
        "LatAcc", "LonAcc", "Steer", "InputThrottle", "Throttle", "FrontBrake",
        "RearBrake", "Clutch", "FrontSusp", "RearSusp", "FrontWheel", "RearWheel",
        "YawVel", "PosX", "PosY",
        # Additional directly recorded / derived fields:
        "Fuel", "RunPos", "TimestampUs", "EventIndex",
        "PosY_3D", "VelocityX", "VelocityY", "VelocityZ",
        "AccelerationX", "AccelerationY", "AccelerationZ",
        "Rot00", "Rot01", "Rot02", "Rot10", "Rot11", "Rot12", "Rot20", "Rot21", "Rot22",
        "Yaw", "Pitch", "Roll", "YawVelRad", "PitchVelRad", "RollVelRad", "PitchVel", "RollVel",
        "PitchRel", "RollRel",
        "FrontSuspLength", "RearSuspLength", "FrontSuspVelocity", "RearSuspVelocity",
        "Crashed", "SteerRaw", "FrontWheelMaterial", "RearWheelMaterial",
        "FrontTreadTempLeft", "FrontTreadTempCenter", "FrontTreadTempRight",
        "RearTreadTempLeft", "RearTreadTempCenter", "RearTreadTempRight",
        "FrontBrakePressure", "RearBrakePressure", "SteerTorque",
        "PitLimiter", "ECUMode", "EngineMapping", "TractionControl", "EngineBraking",
        "AntiWheeling", "ECUState", "RiderLRLean",
    ]

    units = {
        "Time": "s", "Distance": "m", "Engine": "rpm", "CylHeadTemp": "C", "WaterTemp": "C",
        "Gear": "", "Speed": "km/h", "LatAcc": "G", "LonAcc": "G", "Steer": "deg",
        "InputThrottle": "%", "Throttle": "%", "FrontBrake": "bar", "RearBrake": "bar", "Clutch": "%",
        "FrontSusp": "%", "RearSusp": "%", "FrontWheel": "m/s", "RearWheel": "m/s", "YawVel": "deg/s",
        "PosX": "m", "PosY": "m", "Fuel": "l", "RunPos": "", "TimestampUs": "us", "EventIndex": "",
        "PosY_3D": "m", "VelocityX": "m/s", "VelocityY": "m/s", "VelocityZ": "m/s",
        "AccelerationX": "m/s^2", "AccelerationY": "m/s^2", "AccelerationZ": "m/s^2",
        "Yaw": "rad", "Pitch": "rad", "Roll": "rad", "YawVelRad": "rad/s", "PitchVelRad": "rad/s", "RollVelRad": "rad/s",
        "PitchVel": "deg/s", "RollVel": "deg/s", "PitchRel": "rad", "RollRel": "rad",
        "FrontSuspLength": "m", "RearSuspLength": "m", "FrontSuspVelocity": "m/s", "RearSuspVelocity": "m/s",
        "Crashed": "", "SteerRaw": "", "FrontWheelMaterial": "", "RearWheelMaterial": "",
        "FrontTreadTempLeft": "C", "FrontTreadTempCenter": "C", "FrontTreadTempRight": "C",
        "RearTreadTempLeft": "C", "RearTreadTempCenter": "C", "RearTreadTempRight": "C",
        "FrontBrakePressure": "bar", "RearBrakePressure": "bar", "SteerTorque": "Nm",
        "PitLimiter": "", "ECUMode": "", "EngineMapping": "", "TractionControl": "", "EngineBraking": "",
        "AntiWheeling": "", "ECUState": "", "RiderLRLean": "",
    }
    for c in columns:
        if c.startswith("Rot"):
            units[c] = ""

    metadata_rows = [
        ["Format", "PiBoSo CSV File"],
        ["Venue", meta.get("track_name", "")],
        ["Vehicle", meta.get("bike_name", "")],
        ["User", meta.get("rider_name", "")],
        ["Data Source", "GP Bikes"],
        ["Comment", "Converted from MXBHREC binary recording"],
        ["Date", start_dt.strftime("%m/%d/%y")],
        ["Time", start_dt.strftime("%H:%M:%S")],
        ["Sample Rate", sample_rate],
        ["Duration", f"{duration:.3f}"],
        ["Segment", "Session"],
        ["Beacon Markers", ""],
    ]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        for r in metadata_rows:
            w.writerow(r)
        w.writerow([])
        w.writerow(columns)
        w.writerow([units.get(c, "") for c in columns])
        w.writerow([])
        for row in rows:
            w.writerow([fmt_value(row.get(c, "")) for c in columns])

    print(f"Wrote {output_path}")
    print(f"Telemetry rows: {len(rows)}")
    print(f"Estimated sample rate: {sample_rate or 'unknown'} Hz")


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        print("usage: python mxbrec_gpb_to_piboso_csv.py recording.mxbrec [output.csv]")
        sys.exit(2)
    write_piboso_csv(sys.argv[1], sys.argv[2] if len(sys.argv) == 3 else None)
