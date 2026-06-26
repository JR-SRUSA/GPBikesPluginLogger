#!/usr/bin/env python3
"""
mxbrec_gpb_to_piboso_csv_lap_scaled.py

Converts .mxbrec files written by gpb_binary_recorder.c into a PiBoSo-style CSV,
with per-lap time scaling against the official RunLap laptimes.

Key behavior:
- Beacon Markers are written as cumulative official lap-end times from RunLap events.
- Output Time is corrected/scaled per lap.
- RawRunTime and timestamp_ms are preserved.
- Lap boundary raw time is interpolated from RunPos wrap, e.g. 0.9978 -> 0.0013.
- For each lap, RawLapTime = interpolated raw lap duration.
- CorrectLapTime = official RunLap laptime.
- Each sample's corrected lap time is scaled as:
    CorrectedLapTimeInLap = raw_fraction_in_lap * CorrectLapTime
    Time = cumulative_official_time_before_lap + CorrectedLapTimeInLap

Notes:
- This assumes RunLap events correspond in order to detected telemetry lap wraps.
- If the first recording starts mid-lap, the first corrected lap may be partial. The script
  still handles it, but best results come from starting recording before crossing start/finish.
"""

import csv
import math
import os
import statistics
import struct
import sys
from bisect import bisect_right
from datetime import datetime, timezone

HEADER_FMT = "<8sIIQQI32s4x"   # MSVC RecordingHeader size = 72 bytes
EVENT_HEADER_FMT = "<IIQ"
BIKE_DATA_SIZE = 256
G = 9.80665
RAD_TO_DEG = 180.0 / math.pi

EVENT_STARTUP = 1
EVENT_SHUTDOWN = 2
EVENT_EVENT_INIT = 3
EVENT_RUN_INIT = 5
EVENT_RUN_LAP = 9
EVENT_RUN_SPLIT = 10
EVENT_RUN_TELEMETRY = 11


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
    out["steer_lock"], o = f32(buf, o)
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


def decode_lap(buf):
    lap_num, invalid, lap_time_ms, best = struct.unpack_from("<iiii", buf, 0)
    return {
        "lap_num": lap_num,
        "invalid": invalid,
        "lap_time_ms": lap_time_ms,
        "best": best,
        "lap_time_s": lap_time_ms / 1000.0,
    }


def decode_split(buf):
    split, split_time_ms, best_diff_ms = struct.unpack_from("<iii", buf, 0)
    return {
        "split": split,
        "split_time_ms": split_time_ms,
        "best_diff_ms": best_diff_ms,
        "split_time_s": split_time_ms / 1000.0,
    }


def decode_bike_data(buf):
    o = 0
    d = {}
    d["Engine"], o = i32(buf, o)
    d["CylHeadTemp"], o = f32(buf, o)
    d["WaterTemp"], o = f32(buf, o)
    d["Gear"], o = i32(buf, o)
    d["Fuel"], o = f32(buf, o)
    d["Speed"], o = f32(buf, o)
    d["PosX"], o = f32(buf, o)
    d["PosY_3D"], o = f32(buf, o)
    d["PosY"], o = f32(buf, o)  # PiBoSo CSV's 2D Y is GPB's Z coordinate
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
    d["FrontBrake"], o = f32(buf, o)
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

    # PiBoSo-ish derived channels. These are raw/world-axis approximations unless
    # you choose to transform them into bike-local axes later.
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


def estimate_sample_rate_from_raw(rows):
    if len(rows) < 2:
        return ""
    times = [r["RawRunTime"] for r in rows]
    diffs = [b - a for a, b in zip(times, times[1:]) if b > a]
    if not diffs:
        return ""
    med = statistics.median(diffs)
    if med <= 0:
        return ""
    hz = round(1.0 / med)
    return str(int(hz)) if hz > 0 else ""


def detect_lap_boundaries(rows):
    """
    Return list of interpolated boundary dicts for RunPos wraps.
    boundary raw time is interpolated between previous and current sample.
    """
    boundaries = []
    for i in range(1, len(rows)):
        prev = rows[i - 1]
        cur = rows[i]
        p0 = prev["RunPos"]
        p1 = cur["RunPos"]
        # Start/finish wrap. Thresholds avoid false positives from jitter.
        if p0 > 0.5 and p1 < 0.5:
            denom = (1.0 - p0) + p1
            frac = (1.0 - p0) / denom if denom > 0 else 0.5
            boundary_raw = prev["RawRunTime"] + frac * (cur["RawRunTime"] - prev["RawRunTime"])
            boundary_ts_ms = prev["timestamp_ms"] + frac * (cur["timestamp_ms"] - prev["timestamp_ms"])
            boundaries.append({
                "after_sample_index": i - 1,
                "before_sample_index": i,
                "raw_time": boundary_raw,
                "timestamp_ms": boundary_ts_ms,
                "prev_run_pos": p0,
                "next_run_pos": p1,
                "interp_fraction": frac,
            })
    return boundaries


def apply_per_lap_scaling(rows, laps):
    """
    Correct rows in place.

    The official RunLap list contains completed-lap durations. The detected raw lap
    boundaries define raw lap segments. For N official laps and M telemetry boundaries,
    we apply correction to the first min(N, M) completed laps. Samples after the last
    corrected boundary are left on a best-effort continuation using the last known scale.
    """
    if not rows:
        return []

    boundaries = detect_lap_boundaries(rows)
    official_laps = [lap for lap in laps if lap.get("lap_time_s", 0) > 0]

    # Raw lap starts/ends. Start is the first telemetry raw time; ends are wrapped boundaries.
    # If recording starts cleanly at beginning of run, first raw start will be near zero.
    raw_starts = [rows[0]["RawRunTime"]] + [b["raw_time"] for b in boundaries]
    raw_ends = [b["raw_time"] for b in boundaries] + [rows[-1]["RawRunTime"]]

    # Corrected cumulative starts from official lap times.
    cumulative = [0.0]
    for lap in official_laps:
        cumulative.append(cumulative[-1] + lap["lap_time_s"])

    completed_count = min(len(official_laps), len(boundaries))

    # Build per-completed-lap calibration records.
    calibrations = []
    for lap_idx in range(completed_count):
        raw_start = raw_starts[lap_idx]
        raw_end = raw_ends[lap_idx]
        raw_lap = max(0.000001, raw_end - raw_start)
        correct_lap = official_laps[lap_idx]["lap_time_s"]
        calibrations.append({
            "lap_index": lap_idx,
            "lap_number": official_laps[lap_idx].get("lap_num", lap_idx + 1),
            "raw_start": raw_start,
            "raw_end": raw_end,
            "raw_lap_time": raw_lap,
            "correct_cum_start": cumulative[lap_idx],
            "correct_lap_time": correct_lap,
            "correct_cum_end": cumulative[lap_idx + 1],
            "scale": correct_lap / raw_lap,
        })

    # Add a best-effort segment for samples after the last completed official lap.
    # This keeps Time monotonic for an in-progress final lap.
    if completed_count < len(raw_starts):
        lap_idx = completed_count
        raw_start = raw_starts[lap_idx]
        raw_end = raw_ends[lap_idx] if lap_idx < len(raw_ends) else rows[-1]["RawRunTime"]
        raw_lap = max(0.000001, raw_end - raw_start)
        if calibrations:
            # Use the previous lap's scale if no official final lap exists yet.
            scale = calibrations[-1]["scale"]
            correct_lap = raw_lap * scale
            correct_cum_start = calibrations[-1]["correct_cum_end"]
        else:
            scale = 1.0
            correct_lap = raw_lap
            correct_cum_start = 0.0
        calibrations.append({
            "lap_index": lap_idx,
            "lap_number": lap_idx + 1,
            "raw_start": raw_start,
            "raw_end": raw_end,
            "raw_lap_time": raw_lap,
            "correct_cum_start": correct_cum_start,
            "correct_lap_time": correct_lap,
            "correct_cum_end": correct_cum_start + correct_lap,
            "scale": scale,
            "estimated": True,
        })

    raw_start_list = [c["raw_start"] for c in calibrations]

    for row in rows:
        rt = row["RawRunTime"]
        ci = bisect_right(raw_start_list, rt) - 1
        if ci < 0:
            ci = 0
        if ci >= len(calibrations):
            ci = len(calibrations) - 1
        c = calibrations[ci]
        raw_lap_time_at_sample = rt - c["raw_start"]
        if raw_lap_time_at_sample < 0:
            raw_lap_time_at_sample = 0.0
        # Clamp only completed/non-estimated laps to the official end. Do not clamp the live final lap.
        fraction = raw_lap_time_at_sample / c["raw_lap_time"] if c["raw_lap_time"] > 0 else 0.0
        if not c.get("estimated"):
            fraction = max(0.0, min(1.0, fraction))
        corrected_lap_time_at_sample = fraction * c["correct_lap_time"]
        row["LapIndex"] = c["lap_index"]
        row["LapNumber"] = c["lap_number"]
        row["RawLapTime"] = c["raw_lap_time"]
        row["CorrectLapTime"] = c["correct_lap_time"]
        row["LapScale"] = c["scale"]
        row["RawLapTimeAtSample"] = raw_lap_time_at_sample
        row["CorrectedLapTimeAtSample"] = corrected_lap_time_at_sample
        row["Time"] = c["correct_cum_start"] + corrected_lap_time_at_sample

    return boundaries


def read_recording(path):
    header_size = struct.calcsize(HEADER_FMT)
    event_header_size = struct.calcsize(EVENT_HEADER_FMT)
    telemetry_rows = []
    meta = {}
    session = {}
    laps = []
    splits = []

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

            if et == EVENT_EVENT_INIT and size >= 624:
                meta.update(decode_event_init(payload))
            elif et == EVENT_RUN_INIT and size >= 116:
                session.update(decode_session(payload))
            elif et == EVENT_RUN_LAP and size >= 16:
                lap = decode_lap(payload)
                lap["event_index"] = idx
                lap["timestamp_ms"] = ts / 1000.0
                laps.append(lap)
            elif et == EVENT_RUN_SPLIT and size >= 12:
                split = decode_split(payload)
                split["event_index"] = idx
                split["timestamp_ms"] = ts / 1000.0
                splits.append(split)
            elif et == EVENT_RUN_TELEMETRY and size >= BIKE_DATA_SIZE + 8:
                bike = payload[:BIKE_DATA_SIZE]
                run_time, run_pos = struct.unpack_from("<ff", payload, size - 8)
                row = decode_bike_data(bike)
                row["RawRunTime"] = run_time
                row["RunPos"] = run_pos
                row["timestamp_ms"] = ts / 1000.0
                row["EventIndex"] = idx
                telemetry_rows.append(row)

    track_length = float(meta.get("track_length") or 0.0)
    steer_lock = float(meta.get("steer_lock") or 0.0)
    front_susp_max = float(meta.get("front_susp_max_travel") or 0.0)
    rear_susp_max = float(meta.get("rear_susp_max_travel") or 0.0)

    for row in telemetry_rows:
        row["Distance"] = row["RunPos"] * track_length if track_length > 0 else row["RunPos"]
        row["Steer"] = row["SteerRaw"] * steer_lock if steer_lock > 0 else row["SteerRaw"]
        row["FrontSusp"] = (row["FrontSuspLength"] / front_susp_max * 100.0) if front_susp_max > 0 else row["FrontSuspLength"]
        row["RearSusp"] = (row["RearSuspLength"] / rear_susp_max * 100.0) if rear_susp_max > 0 else row["RearSuspLength"]

    boundaries = apply_per_lap_scaling(telemetry_rows, laps)
    return header, meta, session, laps, splits, boundaries, telemetry_rows


def write_aux_csv(path, rows):
    if not rows:
        return
    fields = []
    for row in rows:
        for k in row:
            if k not in fields:
                fields.append(k)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def cumulative_beacons(laps):
    acc = 0.0
    vals = []
    for lap in laps:
        lt = lap.get("lap_time_s", 0)
        if lt > 0:
            acc += lt
            vals.append(acc)
    return vals


def write_piboso_csv(input_path, output_path=None):
    header, meta, session, laps, splits, boundaries, rows = read_recording(input_path)
    if output_path is None:
        base, _ = os.path.splitext(input_path)
        output_path = base + "_piboso_lap_scaled.csv"
    else:
        base, _ = os.path.splitext(output_path)

    start_dt = datetime.fromtimestamp(header["start_us"] / 1_000_000, tz=timezone.utc).astimezone()
    duration = max(0.0, (header["end_us"] - header["start_us"]) / 1_000_000.0)
    sample_rate = estimate_sample_rate_from_raw(rows)
    beacons = cumulative_beacons(laps)
    beacon_str = ",".join(f"{b:.3f}" for b in beacons)

    columns = [
        "Time", "Distance", "Engine", "CylHeadTemp", "WaterTemp", "Gear", "Speed",
        "LatAcc", "LonAcc", "Steer", "InputThrottle", "Throttle", "FrontBrake",
        "RearBrake", "Clutch", "FrontSusp", "RearSusp", "FrontWheel", "RearWheel",
        "YawVel", "PosX", "PosY",
        # Alignment / traceability fields requested:
        "timestamp_ms", "RawRunTime", "RunPos", "LapIndex", "LapNumber",
        "RawLapTime", "CorrectLapTime", "LapScale", "RawLapTimeAtSample", "CorrectedLapTimeAtSample",
        # Additional directly recorded / derived fields:
        "Fuel", "EventIndex", "PosY_3D", "VelocityX", "VelocityY", "VelocityZ",
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
        "PosX": "m", "PosY": "m", "timestamp_ms": "ms", "RawRunTime": "s", "RunPos": "",
        "LapIndex": "", "LapNumber": "", "RawLapTime": "s", "CorrectLapTime": "s", "LapScale": "",
        "RawLapTimeAtSample": "s", "CorrectedLapTimeAtSample": "s", "Fuel": "l", "EventIndex": "",
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
        ["Comment", "Converted from MXBHREC binary recording; Time is per-lap scaled to RunLap laptimes"],
        ["Date", start_dt.strftime("%m/%d/%y")],
        ["Time", start_dt.strftime("%H:%M:%S")],
        ["Sample Rate", sample_rate],
        ["Duration", f"{duration:.3f}"],
        ["Segment", "Session"],
        ["Beacon Markers", beacon_str],
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

    # Helpful diagnostics next to output.
    boundaries_rows = []
    for i, b in enumerate(boundaries):
        boundaries_rows.append({"boundary_index": i, **b})
    write_aux_csv(base + "_lap_boundaries.csv", boundaries_rows)
    write_aux_csv(base + "_laps_used.csv", laps)
    write_aux_csv(base + "_splits_used.csv", splits)

    print(f"Wrote {output_path}")
    print(f"Telemetry rows: {len(rows)}")
    print(f"Detected lap boundaries: {len(boundaries)}")
    print(f"Official RunLap records: {len(laps)}")
    print(f"Beacon markers: {beacon_str if beacon_str else '(none)'}")
    print(f"Estimated sample rate: {sample_rate or 'unknown'} Hz")


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        print("usage: python mxbrec_gpb_to_piboso_csv_lap_scaled.py recording.mxbrec [output.csv]")
        sys.exit(2)
    write_piboso_csv(sys.argv[1], sys.argv[2] if len(sys.argv) == 3 else None)
