#!/usr/bin/env python3
"""
mxbrec_gpb_to_csv.py
Converts .mxbrec files written by gpb_binary_recorder.c into CSV files.

Outputs:
  - <base>_events.csv: every event header
  - <base>_telemetry.csv: decoded RunTelemetry rows
  - <base>_laps.csv: RunLap rows
  - <base>_splits.csv: RunSplit rows
"""
import csv
import os
import struct
import sys

EVENT_NAMES = {
    0: "None", 1: "Startup", 2: "Shutdown", 3: "EventInit", 4: "EventDeinit",
    5: "RunInit", 6: "RunDeinit", 7: "RunStart", 8: "RunStop", 9: "RunLap",
    10: "RunSplit", 11: "RunTelemetry", 12: "DrawInit", 13: "Draw",
    14: "TrackCenterline", 15: "RaceEvent", 16: "RaceDeinit", 17: "RaceSession",
    18: "RaceSessionState", 19: "RaceAddEntry", 20: "RaceRemoveEntry", 21: "RaceLap",
    22: "RaceSplit", 23: "RaceHoleshot", 24: "RaceClassification", 25: "RaceTrackPosition",
    26: "RaceCommunication", 27: "RaceVehicleData",
}

# MSVC layout for RecordingHeader is 72 bytes because of tail padding to 8-byte alignment.
HEADER_FMT = "<8sIIQQI32s4x"
EVENT_HEADER_FMT = "<IIQ"
BIKE_DATA_SIZE = 256


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
    # SPluginsBikeEvent_t, useful metadata for joining later if desired.
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
    vals = struct.unpack_from("<iiii", buf, 0)
    return {"lap_num": vals[0], "invalid": vals[1], "lap_time_ms": vals[2], "best": vals[3]}


def decode_split(buf):
    vals = struct.unpack_from("<iii", buf, 0)
    return {"split": vals[0], "split_time_ms": vals[1], "best_diff_ms": vals[2]}


def decode_bike_data(buf):
    o = 0
    d = {}
    d["rpm"], o = i32(buf, o)
    d["engine_temperature"], o = f32(buf, o)
    d["water_temperature"], o = f32(buf, o)
    d["gear"], o = i32(buf, o)
    d["fuel"], o = f32(buf, o)
    d["speedometer"], o = f32(buf, o)
    d["pos_x"], o = f32(buf, o); d["pos_y"], o = f32(buf, o); d["pos_z"], o = f32(buf, o)
    d["velocity_x"], o = f32(buf, o); d["velocity_y"], o = f32(buf, o); d["velocity_z"], o = f32(buf, o)
    d["acceleration_x"], o = f32(buf, o); d["acceleration_y"], o = f32(buf, o); d["acceleration_z"], o = f32(buf, o)
    rot, o = f32s(buf, o, 9)
    for r in range(3):
        for c in range(3):
            d[f"rot_{r}{c}"] = rot[r*3+c]
    d["yaw"], o = f32(buf, o); d["pitch"], o = f32(buf, o); d["roll"], o = f32(buf, o)
    d["yaw_velocity"], o = f32(buf, o); d["pitch_velocity"], o = f32(buf, o); d["roll_velocity"], o = f32(buf, o)
    d["pitch_rel"], o = f32(buf, o); d["roll_rel"], o = f32(buf, o)
    susp_len, o = f32s(buf, o, 2)
    d["front_susp_length"] = susp_len[0]; d["rear_susp_length"] = susp_len[1]
    susp_vel, o = f32s(buf, o, 2)
    d["front_susp_velocity"] = susp_vel[0]; d["rear_susp_velocity"] = susp_vel[1]
    d["crashed"], o = i32(buf, o)
    d["steer"], o = f32(buf, o)
    d["input_throttle"], o = f32(buf, o)
    d["throttle"], o = f32(buf, o)
    d["front_brake"], o = f32(buf, o)
    d["rear_brake"], o = f32(buf, o)
    d["clutch"], o = f32(buf, o)
    wheel_speed, o = f32s(buf, o, 2)
    d["front_wheel_speed"] = wheel_speed[0]; d["rear_wheel_speed"] = wheel_speed[1]
    mats, o = i32s(buf, o, 2)
    d["front_wheel_material"] = mats[0]; d["rear_wheel_material"] = mats[1]
    tread, o = f32s(buf, o, 6)
    d["front_tread_temp_left"] = tread[0]; d["front_tread_temp_center"] = tread[1]; d["front_tread_temp_right"] = tread[2]
    d["rear_tread_temp_left"] = tread[3]; d["rear_tread_temp_center"] = tread[4]; d["rear_tread_temp_right"] = tread[5]
    brake_pressure, o = f32s(buf, o, 2)
    d["front_brake_pressure"] = brake_pressure[0]; d["rear_brake_pressure"] = brake_pressure[1]
    d["steer_torque"], o = f32(buf, o)
    d["pit_limiter"], o = i32(buf, o)
    d["ecu_mode"], o = i32(buf, o)
    d["engine_mapping"] = cstr(buf[o:o+3]); o += 3
    o += 1  # MSVC padding before next int
    d["traction_control"], o = i32(buf, o)
    d["engine_braking"], o = i32(buf, o)
    d["anti_wheeling"], o = i32(buf, o)
    d["ecu_state"], o = i32(buf, o)
    d["rider_lr_lean"], o = f32(buf, o)
    d["decoded_size"] = o
    return d


def write_csv(path, rows):
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
    print(f"wrote {path}: {len(rows)} rows")


def convert(path):
    base, _ = os.path.splitext(path)
    header_size = struct.calcsize(HEADER_FMT)
    event_header_size = struct.calcsize(EVENT_HEADER_FMT)
    events, telemetry, laps, splits, metadata, sessions = [], [], [], [], [], []

    with open(path, "rb") as f:
        header_raw = f.read(header_size)
        if len(header_raw) != header_size:
            raise RuntimeError("file is too small for RecordingHeader")
        magic, version, num_events, start_us, end_us, flags, _ = struct.unpack(HEADER_FMT, header_raw)
        if magic != b"MXBHREC\x00":
            raise RuntimeError(f"bad magic: {magic!r}")
        print(f"version={version} events={num_events} duration_s={(end_us-start_us)/1_000_000:.3f} flags=0x{flags:08x}")

        for idx in range(num_events):
            raw = f.read(event_header_size)
            if len(raw) != event_header_size:
                print(f"short read at event header {idx}")
                break
            et, size, ts = struct.unpack(EVENT_HEADER_FMT, raw)
            payload = f.read(size)
            if len(payload) != size:
                print(f"short read at event payload {idx}")
                break

            name = EVENT_NAMES.get(et, "Unknown")
            events.append({"index": idx, "timestamp_us": ts, "event_type": et, "event_name": name, "data_size": size})

            common = {"event_index": idx, "timestamp_us": ts}
            if et == 3 and size >= 624:  # EventInit
                row = dict(common); row.update(decode_event_init(payload)); metadata.append(row)
            elif et == 5 and size >= 116:  # RunInit / session
                row = dict(common); row.update(decode_session(payload)); sessions.append(row)
            elif et == 9 and size >= 16:
                row = dict(common); row.update(decode_lap(payload)); laps.append(row)
            elif et == 10 and size >= 12:
                row = dict(common); row.update(decode_split(payload)); splits.append(row)
            elif et == 11 and size >= BIKE_DATA_SIZE + 8:
                bike = payload[:BIKE_DATA_SIZE]
                run_time, run_pos = struct.unpack_from("<ff", payload, size - 8)
                row = dict(common)
                row["run_time"] = run_time
                row["run_pos"] = run_pos
                row["bike_data_size"] = size - 8
                row.update(decode_bike_data(bike))
                telemetry.append(row)

    write_csv(base + "_events.csv", events)
    write_csv(base + "_telemetry.csv", telemetry)
    write_csv(base + "_laps.csv", laps)
    write_csv(base + "_splits.csv", splits)
    write_csv(base + "_event_metadata.csv", metadata)
    write_csv(base + "_sessions.csv", sessions)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python mxbrec_gpb_to_csv.py recording.mxbrec")
        sys.exit(2)
    convert(sys.argv[1])
