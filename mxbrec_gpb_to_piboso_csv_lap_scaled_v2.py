#!/usr/bin/env python3
"""Convert GPB .mxbrec to PiBoSo-style CSV with outlap-aware per-lap scaling."""
import csv, math, os, statistics, struct, sys
from bisect import bisect_right
from datetime import datetime, timezone

HEADER_FMT = "<8sIIQQI32s4x"
EVENT_HEADER_FMT = "<IIQ"
BIKE_DATA_SIZE = 256
G = 9.80665
RAD_TO_DEG = 180.0 / math.pi
EVENT_EVENT_INIT = 3
EVENT_RUN_INIT = 5
EVENT_RUN_LAP = 9
EVENT_RUN_SPLIT = 10
EVENT_RUN_TELEMETRY = 11

def f32(b,o): return struct.unpack_from('<f', b, o)[0], o+4
def i32(b,o): return struct.unpack_from('<i', b, o)[0], o+4
def f32s(b,o,n): return list(struct.unpack_from('<'+'f'*n, b, o)), o+4*n
def i32s(b,o,n): return list(struct.unpack_from('<'+'i'*n, b, o)), o+4*n
def cstr(raw): return raw.split(b'\0',1)[0].decode('utf-8', errors='replace')

def decode_event_init(b):
    o=0; d={}
    d['rider_name']=cstr(b[o:o+100]); o+=100
    d['bike_id']=cstr(b[o:o+100]); o+=100
    d['bike_name']=cstr(b[o:o+100]); o+=100
    d['number_of_gears'],o=i32(b,o); d['max_rpm'],o=i32(b,o); d['limiter'],o=i32(b,o); d['shift_rpm'],o=i32(b,o)
    d['engine_opt_temperature'],o=f32(b,o); alarms,o=f32s(b,o,2)
    d['engine_temperature_alarm_low'],d['engine_temperature_alarm_high']=alarms
    d['max_fuel'],o=f32(b,o); susp,o=f32s(b,o,2)
    d['front_susp_max_travel'],d['rear_susp_max_travel']=susp
    d['steer_lock'],o=f32(b,o)
    d['category']=cstr(b[o:o+100]); o+=100
    d['track_id']=cstr(b[o:o+100]); o+=100
    d['track_name']=cstr(b[o:o+100]); o+=100
    d['track_length'],o=f32(b,o); d['type'],o=i32(b,o)
    return d

def decode_session(b):
    o=0; d={}
    d['session'],o=i32(b,o); d['conditions'],o=i32(b,o); d['air_temperature'],o=f32(b,o); d['track_temperature'],o=f32(b,o)
    d['setup_file_name']=cstr(b[o:o+100])
    return d

def decode_lap(b):
    lap_num, invalid, lap_time_ms, best = struct.unpack_from('<iiii', b, 0)
    return {'lap_num':lap_num,'invalid':invalid,'lap_time_ms':lap_time_ms,'best':best,'lap_time_s':lap_time_ms/1000.0}

def decode_split(b):
    split, split_time_ms, best_diff_ms = struct.unpack_from('<iii', b, 0)
    return {'split':split,'split_time_ms':split_time_ms,'best_diff_ms':best_diff_ms,'split_time_s':split_time_ms/1000.0}

def decode_bike_data(b):
    o=0; d={}
    d['Engine'],o=i32(b,o); d['CylHeadTemp'],o=f32(b,o); d['WaterTemp'],o=f32(b,o); d['Gear'],o=i32(b,o)
    d['Fuel'],o=f32(b,o); d['Speed'],o=f32(b,o)
    d['PosX'],o=f32(b,o); d['PosY_3D'],o=f32(b,o); d['PosY'],o=f32(b,o)
    d['VelocityX'],o=f32(b,o); d['VelocityY'],o=f32(b,o); d['VelocityZ'],o=f32(b,o)
    d['AccelerationX'],o=f32(b,o); d['AccelerationY'],o=f32(b,o); d['AccelerationZ'],o=f32(b,o)
    rot,o=f32s(b,o,9)
    for r in range(3):
        for c in range(3): d[f'Rot{r}{c}']=rot[r*3+c]
    d['Yaw'],o=f32(b,o); d['Pitch'],o=f32(b,o); d['Roll'],o=f32(b,o)
    d['YawVelRad'],o=f32(b,o); d['PitchVelRad'],o=f32(b,o); d['RollVelRad'],o=f32(b,o)
    d['YawVel']=d['YawVelRad']*RAD_TO_DEG; d['PitchVel']=d['PitchVelRad']*RAD_TO_DEG; d['RollVel']=d['RollVelRad']*RAD_TO_DEG
    d['PitchRel'],o=f32(b,o); d['RollRel'],o=f32(b,o)
    susp,o=f32s(b,o,2); d['FrontSuspLength'],d['RearSuspLength']=susp
    suspv,o=f32s(b,o,2); d['FrontSuspVelocity'],d['RearSuspVelocity']=suspv
    d['Crashed'],o=i32(b,o); d['SteerRaw'],o=f32(b,o); d['InputThrottle'],o=f32(b,o); d['Throttle'],o=f32(b,o)
    d['FrontBrake'],o=f32(b,o); d['RearBrake'],o=f32(b,o); d['Clutch'],o=f32(b,o)
    wh,o=f32s(b,o,2); d['FrontWheel'],d['RearWheel']=wh
    mats,o=i32s(b,o,2); d['FrontWheelMaterial'],d['RearWheelMaterial']=mats
    tread,o=f32s(b,o,6)
    d['FrontTreadTempLeft'],d['FrontTreadTempCenter'],d['FrontTreadTempRight']=tread[:3]
    d['RearTreadTempLeft'],d['RearTreadTempCenter'],d['RearTreadTempRight']=tread[3:]
    bp,o=f32s(b,o,2); d['FrontBrakePressure'],d['RearBrakePressure']=bp
    d['SteerTorque'],o=f32(b,o); d['PitLimiter'],o=i32(b,o); d['ECUMode'],o=i32(b,o)
    d['EngineMapping']=cstr(b[o:o+3]); o+=4  # char[3] + MSVC padding
    d['TractionControl'],o=i32(b,o); d['EngineBraking'],o=i32(b,o); d['AntiWheeling'],o=i32(b,o); d['ECUState'],o=i32(b,o)
    d['RiderLRLean'],o=f32(b,o)
    d['LatAcc']=d['AccelerationX']/G; d['LonAcc']=d['AccelerationZ']/G
    return d

def detect_lap_boundaries(rows):
    out=[]
    for i in range(1,len(rows)):
        a,b=rows[i-1],rows[i]; p0,p1=a['RunPos'],b['RunPos']
        if p0>0.5 and p1<0.5:
            denom=(1-p0)+p1
            frac=(1-p0)/denom if denom>0 else 0.5
            rt=a['RawRunTime']+frac*(b['RawRunTime']-a['RawRunTime'])
            ts=a['timestamp_ms']+frac*(b['timestamp_ms']-a['timestamp_ms'])
            out.append({'boundary_index':len(out),'after_sample_index':i-1,'before_sample_index':i,'raw_time':rt,'timestamp_ms':ts,'prev_run_pos':p0,'next_run_pos':p1,'interp_fraction':frac})
    return out

def apply_outlap_aware_scaling(rows,laps):
    boundaries=detect_lap_boundaries(rows)
    official=[x for x in laps if x.get('lap_time_s',0)>0]
    if not rows: return boundaries, [], 0
    raw_starts=[rows[0]['RawRunTime']]+[b['raw_time'] for b in boundaries]
    raw_ends=[b['raw_time'] for b in boundaries]+[rows[-1]['RawRunTime']]
    # Key fix: if there are more completed raw segments than official RunLap records, treat the leading extras as outlaps.
    outlap_segments=max(0, len(boundaries)-len(official))
    cal=[]; cursor=0.0
    for seg in range(outlap_segments):
        raw=max(1e-6, raw_ends[seg]-raw_starts[seg])
        cal.append({'lap_index':seg,'lap_number':0,'is_outlap':1,'raw_start':raw_starts[seg],'raw_end':raw_ends[seg],'raw_lap_time':raw,'correct_lap_time':raw,'correct_cum_start':cursor,'correct_cum_end':cursor+raw,'scale':1.0})
        cursor+=raw
    mapped=0
    for oi,lap in enumerate(official):
        seg=outlap_segments+oi
        if seg>=len(raw_starts): break
        raw=max(1e-6, raw_ends[seg]-raw_starts[seg])
        cor=lap['lap_time_s']
        cal.append({'lap_index':seg,'lap_number':lap.get('lap_num',oi+1),'is_outlap':0,'official_lap_index':oi,'raw_start':raw_starts[seg],'raw_end':raw_ends[seg],'raw_lap_time':raw,'correct_lap_time':cor,'correct_cum_start':cursor,'correct_cum_end':cursor+cor,'scale':cor/raw})
        cursor+=cor; mapped+=1
    nextseg=outlap_segments+mapped
    if nextseg<len(raw_starts) and nextseg not in {c['lap_index'] for c in cal}:
        raw=max(1e-6, raw_ends[nextseg]-raw_starts[nextseg])
        scale=cal[-1]['scale'] if cal else 1.0
        cor=raw*scale
        cal.append({'lap_index':nextseg,'lap_number':nextseg+1,'is_outlap':0,'estimated':1,'raw_start':raw_starts[nextseg],'raw_end':raw_ends[nextseg],'raw_lap_time':raw,'correct_lap_time':cor,'correct_cum_start':cursor,'correct_cum_end':cursor+cor,'scale':scale})
    if not cal:
        raw=max(1e-6, rows[-1]['RawRunTime']-rows[0]['RawRunTime'])
        cal=[{'lap_index':0,'lap_number':0,'is_outlap':0,'estimated':1,'raw_start':rows[0]['RawRunTime'],'raw_end':rows[-1]['RawRunTime'],'raw_lap_time':raw,'correct_lap_time':raw,'correct_cum_start':0.0,'correct_cum_end':raw,'scale':1.0}]
    starts=[c['raw_start'] for c in cal]
    for r in rows:
        ci=max(0,min(bisect_right(starts,r['RawRunTime'])-1,len(cal)-1)); c=cal[ci]
        raw_at=max(0.0,r['RawRunTime']-c['raw_start'])
        frac=raw_at/c['raw_lap_time'] if c['raw_lap_time'] else 0.0
        if not c.get('estimated'): frac=max(0.0,min(1.0,frac))
        corr_at=frac*c['correct_lap_time']
        r.update({'LapIndex':c['lap_index'],'LapNumber':c['lap_number'],'IsOutLap':c['is_outlap'],'RawLapTime':c['raw_lap_time'],'CorrectLapTime':c['correct_lap_time'],'LapScale':c['scale'],'RawLapTimeAtSample':raw_at,'CorrectedLapTimeAtSample':corr_at,'Time':c['correct_cum_start']+corr_at})
    return boundaries, cal, outlap_segments

def read_recording(path):
    rows=[]; meta={}; session={}; laps=[]; splits=[]
    with open(path,'rb') as f:
        h=f.read(struct.calcsize(HEADER_FMT))
        magic,version,num_events,start_us,end_us,flags,_=struct.unpack(HEADER_FMT,h)
        if magic!=b'MXBHREC\0': raise RuntimeError(f'Bad magic: {magic!r}')
        header={'version':version,'num_events':num_events,'start_us':start_us,'end_us':end_us,'flags':flags}
        for idx in range(num_events):
            eh=f.read(struct.calcsize(EVENT_HEADER_FMT))
            if len(eh)!=struct.calcsize(EVENT_HEADER_FMT): break
            et,size,ts=struct.unpack(EVENT_HEADER_FMT,eh); payload=f.read(size)
            if len(payload)!=size: break
            if et==EVENT_EVENT_INIT and size>=624: meta.update(decode_event_init(payload))
            elif et==EVENT_RUN_INIT and size>=116: session.update(decode_session(payload))
            elif et==EVENT_RUN_LAP and size>=16:
                x=decode_lap(payload); x.update({'event_index':idx,'timestamp_ms':ts/1000.0}); laps.append(x)
            elif et==EVENT_RUN_SPLIT and size>=12:
                x=decode_split(payload); x.update({'event_index':idx,'timestamp_ms':ts/1000.0}); splits.append(x)
            elif et==EVENT_RUN_TELEMETRY and size>=BIKE_DATA_SIZE+8:
                run_time,run_pos=struct.unpack_from('<ff',payload,size-8)
                r=decode_bike_data(payload[:BIKE_DATA_SIZE]); r.update({'RawRunTime':run_time,'RunPos':run_pos,'timestamp_ms':ts/1000.0,'EventIndex':idx}); rows.append(r)
    tl=float(meta.get('track_length') or 0); sl=float(meta.get('steer_lock') or 0); fs=float(meta.get('front_susp_max_travel') or 0); rs=float(meta.get('rear_susp_max_travel') or 0)
    for r in rows:
        r['Distance']=r['RunPos']*tl if tl>0 else r['RunPos']
        r['Steer']=r['SteerRaw']*sl if sl>0 else r['SteerRaw']
        r['FrontSusp']=r['FrontSuspLength']/fs*100 if fs>0 else r['FrontSuspLength']
        r['RearSusp']=r['RearSuspLength']/rs*100 if rs>0 else r['RearSuspLength']
    boundaries,cal,outlaps=apply_outlap_aware_scaling(rows,laps)
    return header,meta,session,laps,splits,boundaries,cal,outlaps,rows

def fmt(v):
    if v is None: return ''
    if isinstance(v,int): return str(v)
    if isinstance(v,float): return '' if math.isnan(v) or math.isinf(v) else f'{v:.6f}'
    return str(v)

def estimate_rate(rows):
    ds=[b['RawRunTime']-a['RawRunTime'] for a,b in zip(rows,rows[1:]) if b['RawRunTime']>a['RawRunTime']]
    return str(int(round(1/statistics.median(ds)))) if ds and statistics.median(ds)>0 else ''

def write_aux(path, rows):
    if not rows: return
    keys=[]
    for r in rows:
        for k in r:
            if k not in keys: keys.append(k)
    with open(path,'w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=keys); w.writeheader(); w.writerows(rows)

def write_csv(infile,outfile=None):
    header,meta,session,laps,splits,bounds,cal,outlaps,rows=read_recording(infile)
    if outfile is None:
        base,_=os.path.splitext(infile); outfile=base+'_piboso_lap_scaled_v2.csv'
    else: base,_=os.path.splitext(outfile)
    dt=datetime.fromtimestamp(header['start_us']/1_000_000,tz=timezone.utc).astimezone()
    duration=max(0,(header['end_us']-header['start_us'])/1_000_000)
    beacons = [c["correct_cum_end"] for c in cal if not c.get("estimated")]
    columns=['Time','Distance','Engine','CylHeadTemp','WaterTemp','Gear','Speed','LatAcc','LonAcc','Steer','InputThrottle','Throttle','FrontBrake','RearBrake','Clutch','FrontSusp','RearSusp','FrontWheel','RearWheel','YawVel','PosX','PosY','timestamp_ms','RawRunTime','RunPos','LapIndex','LapNumber','IsOutLap','RawLapTime','CorrectLapTime','LapScale','RawLapTimeAtSample','CorrectedLapTimeAtSample','Fuel','EventIndex','PosY_3D','VelocityX','VelocityY','VelocityZ','AccelerationX','AccelerationY','AccelerationZ','Rot00','Rot01','Rot02','Rot10','Rot11','Rot12','Rot20','Rot21','Rot22','Yaw','Pitch','Roll','YawVelRad','PitchVelRad','RollVelRad','PitchVel','RollVel','PitchRel','RollRel','FrontSuspLength','RearSuspLength','FrontSuspVelocity','RearSuspVelocity','Crashed','SteerRaw','FrontWheelMaterial','RearWheelMaterial','FrontTreadTempLeft','FrontTreadTempCenter','FrontTreadTempRight','RearTreadTempLeft','RearTreadTempCenter','RearTreadTempRight','FrontBrakePressure','RearBrakePressure','SteerTorque','PitLimiter','ECUMode','EngineMapping','TractionControl','EngineBraking','AntiWheeling','ECUState','RiderLRLean']
    units={c:'' for c in columns}
    units.update({'Time':'s','Distance':'m','Engine':'rpm','CylHeadTemp':'C','WaterTemp':'C','Speed':'km/h','LatAcc':'G','LonAcc':'G','Steer':'deg','InputThrottle':'%','Throttle':'%','FrontBrake':'bar','RearBrake':'bar','Clutch':'%','FrontSusp':'%','RearSusp':'%','FrontWheel':'m/s','RearWheel':'m/s','YawVel':'deg/s','PosX':'m','PosY':'m','timestamp_ms':'ms','RawRunTime':'s','RawLapTime':'s','CorrectLapTime':'s','RawLapTimeAtSample':'s','CorrectedLapTimeAtSample':'s','Fuel':'l','PosY_3D':'m','VelocityX':'m/s','VelocityY':'m/s','VelocityZ':'m/s','AccelerationX':'m/s^2','AccelerationY':'m/s^2','AccelerationZ':'m/s^2','Yaw':'rad','Pitch':'rad','Roll':'rad','YawVelRad':'rad/s','PitchVelRad':'rad/s','RollVelRad':'rad/s','PitchVel':'deg/s','RollVel':'deg/s','PitchRel':'rad','RollRel':'rad','FrontSuspLength':'m','RearSuspLength':'m','FrontSuspVelocity':'m/s','RearSuspVelocity':'m/s','FrontTreadTempLeft':'C','FrontTreadTempCenter':'C','FrontTreadTempRight':'C','RearTreadTempLeft':'C','RearTreadTempCenter':'C','RearTreadTempRight':'C','FrontBrakePressure':'bar','RearBrakePressure':'bar','SteerTorque':'Nm'})
    metadata=[['Format','PiBoSo CSV File'],['Venue',meta.get('track_name','')],['Vehicle',meta.get('bike_name','')],['User',meta.get('rider_name','')],['Data Source','GP Bikes'],['Comment',f'Converted from MXBHREC; outlap-aware per-lap scaling; outlap_segments={outlaps}'],['Date',dt.strftime('%m/%d/%y')],['Time',dt.strftime('%H:%M:%S')],['Sample Rate',estimate_rate(rows)],['Duration',f'{duration:.3f}'],['Segment','Session'],['Beacon Markers',','.join(f'{x:.3f}' for x in beacons)]]
    with open(outfile,'w',newline='',encoding='utf-8') as f:
        w=csv.writer(f,quoting=csv.QUOTE_ALL)
        for r in metadata: w.writerow(r)
        w.writerow([]); w.writerow(columns); w.writerow([units.get(c,'') for c in columns]); w.writerow([])
        for r in rows: w.writerow([fmt(r.get(c,'')) for c in columns])
    write_aux(base+'_lap_boundaries.csv',bounds); write_aux(base+'_lap_calibrations.csv',cal); write_aux(base+'_laps_used.csv',laps); write_aux(base+'_splits_used.csv',splits)
    print(f'Wrote {outfile}')
    print(f'Telemetry rows: {len(rows)}')
    print(f'Detected lap boundaries: {len(bounds)}')
    print(f'Official RunLap records: {len(laps)}')
    print(f'Auto outlap segments skipped before official laps: {outlaps}')
    print('Beacon markers:', ','.join(f'{x:.3f}' for x in beacons) or '(none)')

if __name__=='__main__':
    if len(sys.argv) not in (2,3):
        print('usage: python mxbrec_gpb_to_piboso_csv_lap_scaled_v2.py recording.mxbrec [output.csv]')
        raise SystemExit(2)
    write_csv(sys.argv[1], sys.argv[2] if len(sys.argv)==3 else None)
