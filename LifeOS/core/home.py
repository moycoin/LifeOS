#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json,re,subprocess,time,threading,ctypes,sys,logging,socket,struct
from concurrent.futures import ThreadPoolExecutor,TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Dict,Optional,Callable,List,Any,Tuple,Set
from enum import Enum
from .types import __version__
try:
    from phue import Bridge
    PHUE_AVAILABLE = True
except ImportError:
    PHUE_AVAILABLE = False
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
class PowerSavingMode(Enum):
    OFF="off";LOW="low";HIGH="high";PICTURE_OFF="pictureOff"
ALLOWED_ROOMS=None
class MonitorController:
    SC_MONITORPOWER=0xF170
    MONITOR_OFF=2
    MONITOR_ON=-1
    HWND_BROADCAST=0xFFFF
    WM_SYSCOMMAND=0x0112
    SM_CMONITORS=80
    ES_CONTINUOUS=0x80000000
    ES_DISPLAY_REQUIRED=0x00000002
    ES_SYSTEM_REQUIRED=0x00000001
    MOUSEEVENTF_LEFTDOWN=0x0002
    MOUSEEVENTF_LEFTUP=0x0004
    def __init__(self,logger:Optional[logging.Logger]=None):
        self._is_windows=sys.platform=='win32'
        self._monitors_off=False
        self._monitor_count=1
        self.logger=logger or logging.getLogger(__name__)
    def get_monitor_count(self)->int:
        if not self._is_windows:return 1
        try:
            count=ctypes.windll.user32.GetSystemMetrics(self.SM_CMONITORS)
            self._monitor_count=max(1,count)
            return self._monitor_count
        except Exception:
            return 1
    def _send_message(self,wparam:int)->bool:
        if not self._is_windows:return False
        try:
            ctypes.windll.user32.SendMessageW(self.HWND_BROADCAST,self.WM_SYSCOMMAND,self.SC_MONITORPOWER,wparam)
            return True
        except Exception as e:
            self.logger.error(f"Monitor control failed: {e}")
            return False
    def _post_message(self,wparam:int)->bool:
        if not self._is_windows:return False
        try:
            ctypes.windll.user32.PostMessageW(self.HWND_BROADCAST,self.WM_SYSCOMMAND,self.SC_MONITORPOWER,wparam)
            return True
        except Exception:
            return False
    def _set_execution_state(self,flags:int)->None:
        if not self._is_windows:return
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(flags)
        except Exception:
            pass
    def _simulate_click(self)->None:
        if not self._is_windows:return
        try:
            ctypes.windll.user32.mouse_event(self.MOUSEEVENTF_LEFTDOWN,0,0,0,0)
            time.sleep(0.01)
            ctypes.windll.user32.mouse_event(self.MOUSEEVENTF_LEFTUP,0,0,0,0)
        except Exception:pass
    def _send_input_key(self)->None:
        if not self._is_windows:return
        try:
            INPUT_KEYBOARD=1;KEYEVENTF_KEYUP=0x0002;VK_SPACE=0x20
            class KEYBDINPUT(ctypes.Structure):
                _fields_=[('wVk',ctypes.c_ushort),('wScan',ctypes.c_ushort),('dwFlags',ctypes.c_ulong),('time',ctypes.c_ulong),('dwExtraInfo',ctypes.POINTER(ctypes.c_ulong))]
            class INPUT(ctypes.Structure):
                _fields_=[('type',ctypes.c_ulong),('ki',KEYBDINPUT),('padding',ctypes.c_ubyte*8)]
            def make_input(vk:int,flags:int=0)->INPUT:
                inp=INPUT();inp.type=INPUT_KEYBOARD;inp.ki.wVk=vk;inp.ki.dwFlags=flags;return inp
            inputs=(INPUT*2)(make_input(VK_SPACE,0),make_input(VK_SPACE,KEYEVENTF_KEYUP))
            ctypes.windll.user32.SendInput(2,ctypes.byref(inputs),ctypes.sizeof(INPUT))
        except Exception as e:
            self.logger.debug(f"SendInput failed: {e}")
    def turn_off(self)->bool:
        self.get_monitor_count()
        if self._send_message(self.MONITOR_OFF):
            self._monitors_off=True
            return True
        return False
    def turn_on(self)->bool:
        if not self._is_windows:return False
        monitor_count=self.get_monitor_count()
        try:
            self._set_execution_state(self.ES_CONTINUOUS|self.ES_DISPLAY_REQUIRED|self.ES_SYSTEM_REQUIRED)
            time.sleep(0.05)
            self._send_input_key()
            time.sleep(0.1)
            self._set_execution_state(self.ES_CONTINUOUS)
            self._monitors_off=False
            return True
        except Exception as e:
            self.logger.error(f"Monitor turn_on failed: {e}")
            self._set_execution_state(self.ES_CONTINUOUS)
            return False
    def is_off(self)->bool:return self._monitors_off
    def monitor_count(self)->int:return self._monitor_count
class BraviaController:
    PACKAGE_MAP={'spotify':'Spotify','netflix':'Netflix','youtube':'YouTube','amazon':'Prime Video','disney':'Disney+','hulu':'Hulu','abema':'ABEMA','tver':'TVer','nhkplus':'NHK+','dazn':'DAZN','twitch':'Twitch','plex':'Plex','kodi':'Kodi','vlc':'VLC','crunchyroll':'Crunchyroll','funimation':'Funimation','tv.sony':'Live TV','settings':'Settings','launcher':'Home'}
    IRCC_VOLUME_UP='AAAAAQAAAAEAAAASAw=='
    IRCC_VOLUME_DOWN='AAAAAQAAAAEAAAATAw=='
    VOL_STEP_INTERVAL=0.25
    VOL_SETTLE_DELAY=0.15
    VOL_MAX_ITERATIONS=120
    MAX_CONSECUTIVE_FAILURES=3
    def __init__(self,ip:str,psk:str,logger:Optional[logging.Logger]=None):
        self.ip,self.psk,self.base_url=ip,psk,f"http://{ip}/sony"
        self._power_state,self._volume,self._app_name,self._power_saving,self._adb_connected=None,None,None,None,False
        self.logger=logger or logging.getLogger(__name__)
        self._consecutive_failures=0
        self._is_healthy=True
        self._reconnect_callback:Optional[Callable]=None
    def set_reconnect_callback(self,callback:Callable)->None:
        self._reconnect_callback=callback
    def _request(self,service:str,method:str,params:list=None,version:str="1.0")->Optional[Dict]:
        if not REQUESTS_AVAILABLE:return None
        try:
            r=requests.post(f"{self.base_url}/{service}",json={"method":method,"params":params or[],"id":1,"version":version},headers={"X-Auth-PSK":self.psk,"Content-Type":"application/json"},timeout=3)
            result=r.json() if r.status_code==200 else None
            if result is not None:
                self._consecutive_failures=0
                self._is_healthy=True
            else:
                self._consecutive_failures+=1
                if self._consecutive_failures>=self.MAX_CONSECUTIVE_FAILURES:
                    self._is_healthy=False
            return result
        except requests.exceptions.Timeout:
            self.logger.debug(f"Bravia request timeout: {method}")
            self._consecutive_failures+=1
            if self._consecutive_failures>=self.MAX_CONSECUTIVE_FAILURES:
                self._is_healthy=False
            return None
        except requests.exceptions.RequestException as e:
            self.logger.debug(f"Bravia request error: {e}")
            self._consecutive_failures+=1
            if self._consecutive_failures>=self.MAX_CONSECUTIVE_FAILURES:
                self._is_healthy=False
            return None
    def ensure_connection(self,trigger:str='periodic')->bool:
        if not self._is_healthy or trigger in ('power_change','app_change'):
            self._adb_connected=False
        result=self._request("system","getPowerStatus")
        if result and "result" in result:
            self._is_healthy=True
            self._consecutive_failures=0
            if self._reconnect_callback:
                self._reconnect_callback()
            return True
        self.logger.warning(f"BRAVIA reconnect failed (trigger={trigger})")
        return False
    def is_healthy(self)->bool:
        return self._is_healthy
    def _send_ircc(self,code:str)->bool:
        if not REQUESTS_AVAILABLE:return False
        try:
            xml=f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body><u:X_SendIRCC xmlns:u="urn:schemas-sony-com:service:IRCC:1"><IRCCCode>{code}</IRCCCode></u:X_SendIRCC></s:Body></s:Envelope>'
            r=requests.post(f"http://{self.ip}/sony/IRCC",data=xml,headers={"X-Auth-PSK":self.psk,"Content-Type":"text/xml; charset=UTF-8","SOAPACTION":'"urn:schemas-sony-com:service:IRCC:1#X_SendIRCC"'},timeout=2.0)
            return r.status_code==200
        except Exception:
            return False
    def _get_app_via_adb(self)->Optional[str]:
        try:
            if not self._adb_connected:
                subprocess.run(['adb','connect',f'{self.ip}:5555'],capture_output=True,timeout=2)
                self._adb_connected=True
            result=subprocess.run(['adb','-s',f'{self.ip}:5555','shell','dumpsys','activity','activities'],capture_output=True,text=True,timeout=2)
            if result.returncode!=0:return None
            for line in result.stdout.split('\n'):
                if 'ResumedActivity' in line or 'mResumedActivity' in line:
                    match=re.search(r'u0\s+([^\s/]+)/',line)
                    if match:
                        package=match.group(1).lower()
                        for key,name in self.PACKAGE_MAP.items():
                            if key in package:return name
                        return package.split('.')[-1].title()
            return None
        except subprocess.TimeoutExpired:
            self.logger.debug("ADB timeout")
            return None
        except Exception as e:
            self.logger.debug(f"ADB error: {e}")
            return None
    def get_power_status(self)->bool:
        result=self._request("system","getPowerStatus")
        if result and "result" in result:
            self._power_state=result["result"][0].get("status")=="active"
            return self._power_state
        return False
    def set_power(self,on:bool)->bool:
        self.ensure_connection('power_change')
        result=self._request("system","setPowerStatus",[{"status":on}])
        if result and "result" in result:
            self._power_state=on
            return True
        return False
    def power_off(self)->bool:return self.set_power(False)
    def power_on(self)->bool:return self.set_power(True)
    def get_volume(self)->int:
        result=self._request("audio","getVolumeInformation")
        if result and "result" in result:
            for item in result["result"][0]:
                if item.get("target")=="speaker":
                    self._volume=item.get("volume",0)
                    return self._volume
        return 0
    def get_playing_content(self)->str:
        result=self._request("avContent","getPlayingContentInfo")
        title=None
        if result:
            if "error" not in result and "result" in result:
                data=result["result"][0]
                title=data.get("title","") or data.get("programTitle","")
        if not title or title=="Unknown":
            adb_result=self._get_app_via_adb()
            if adb_result:title=adb_result
        self._app_name=title if title else "Unknown"
        return self._app_name
    def get_power_saving_mode(self)->str:
        result=self._request("system","getPowerSavingMode")
        if result and "result" in result:
            self._power_saving=result["result"][0].get("mode","off")
            return self._power_saving
        return "off"
    def set_power_saving_mode(self,mode:PowerSavingMode)->bool:
        result=self._request("system","setPowerSavingMode",[{"mode":mode.value}])
        return result is not None and "result" in result
    def set_volume(self,target:int,max_steps:int=100)->bool:
        target=max(0,min(100,target))
        target=target if target%2==0 else target+1
        current=self.get_volume()
        if current is None:
            self.logger.warning("Volume read failed")
            return False
        if current==target:return True
        if abs(current-target)<=1:return True
        iterations=0
        api_retry=0
        reverse_count=0
        while iterations<self.VOL_MAX_ITERATIONS:
            iterations+=1
            diff=target-current
            if abs(diff)<=1:
                self._volume=current
                return True
            direction=1 if diff>0 else -1
            before_vol=current
            ircc=self.IRCC_VOLUME_UP if diff>0 else self.IRCC_VOLUME_DOWN
            self._send_ircc(ircc)
            time.sleep(self.VOL_STEP_INTERVAL)
            time.sleep(self.VOL_SETTLE_DELAY)
            new_vol=self.get_volume()
            if new_vol is None:
                api_retry+=1
                if api_retry>=3:
                    self.logger.warning("Volume API failed repeatedly")
                    break
                continue
            api_retry=0
            actual_change=new_vol-before_vol
            if actual_change!=0 and (actual_change>0)!=(direction>0):
                reverse_count+=1
                if reverse_count>=3:
                    self._volume=new_vol
                    return False
            else:
                reverse_count=0
            current=new_vol
        self._volume=current if current is not None else self._volume
        return abs(current-target)<=1 if current is not None else False
    def get_status(self)->Dict:
        return {'power':self.get_power_status(),'volume':self.get_volume(),'app':self.get_playing_content(),'power_saving':self.get_power_saving_mode()}
class HueController:
    def __init__(self,ip:str,logger:Optional[logging.Logger]=None):
        self.ip=ip
        self._bridge:Optional[Bridge]=None
        self._connected=False
        self._group_cache:Dict[str,int]={}
        self._zone_light_ids:Set[str]=set()
        self.logger=logger or logging.getLogger(__name__)
    def connect(self)->bool:
        if not PHUE_AVAILABLE:return False
        try:
            self._bridge=Bridge(self.ip)
            self._bridge.connect()
            self._connected=True
            self._build_group_cache()
            return True
        except Exception as e:
            self.logger.error(f"Hue connection failed: {e}")
            self._connected=False
            return False
    def _build_group_cache(self):
        if not self._connected or not self._bridge:return
        try:
            self._group_cache.clear()
            self._zone_light_ids.clear()
            for gid,info in self._bridge.get_group().items():
                name=info.get('name','')
                if name:self._group_cache[name]=int(gid) if isinstance(gid,str) and gid.isdigit() else gid
                if info.get('type')=='Zone':self._zone_light_ids.update(str(lid) for lid in info.get('lights',[]))
        except Exception as e:
            self.logger.debug(f"Hue group cache build error: {e}")
    def _safe_set_group(self,group_name:str,param:str,value)->bool:
        if not self._connected or not self._bridge:return False
        try:
            gid=self._group_cache.get(group_name)
            if gid is None:
                self._build_group_cache()
                gid=self._group_cache.get(group_name)
            if gid is None:return False
            self._bridge.set_group(gid,param,value)
            return True
        except Exception as e:
            self.logger.debug(f"Hue set_group error ({group_name}): {e}")
            return False
    def get_room_brightness(self,room_name:str)->float:
        if not self._connected or not self._bridge or not room_name:return 0.0
        try:
            for gid,info in self._bridge.get_group().items():
                if info.get('name')==room_name:
                    action=info.get('action',{})
                    return (action.get('bri',0)/254) if action.get('on') else 0.0
            return 0.0
        except Exception as e:
            self.logger.debug(f"Hue brightness error: {e}")
            return 0.0
    def is_all_lights_off(self,room_name:str)->bool:
        if not self._connected or not self._bridge or not room_name:return False
        try:
            for gid,info in self._bridge.get_group().items():
                if info.get('name')==room_name:return not info.get('state',{}).get('any_on',True)
            return False
        except Exception as e:
            self.logger.debug(f"Hue lights check error: {e}")
            return False
    def get_all_rooms(self,hide_zone_members:bool=False)->Dict:
        if not self._connected or not self._bridge:return {}
        try:
            if not self._zone_light_ids:self._build_group_cache()
            rooms={}
            for gid,info in self._bridge.get_group().items():
                name,gtype=info.get('name',''),info.get('type')
                if gtype not in ('Room','Zone'):continue
                if hide_zone_members and gtype=='Room':
                    room_lights=set(str(lid) for lid in info.get('lights',[]))
                    if room_lights and room_lights.issubset(self._zone_light_ids):continue
                action,state=info.get('action',{}),info.get('state',{})
                is_on=state.get('any_on',False)
                bri=(action.get('bri',0)/254) if is_on else 0.0
                rooms[name]={'on':is_on,'bri':bri,'group_id':gid,'is_zone':gtype=='Zone'}
            return rooms
        except Exception as e:
            self.logger.debug(f"Hue get_all_rooms error: {e}")
            return {}
    def turn_off_except_rooms(self,keep_rooms:List[str])->int:
        if not self._connected or not self._bridge:return 0
        keep_light_ids:Set[str]=set()
        try:
            groups=self._bridge.get_group()
            for gid,info in groups.items():
                if info.get('name','') in keep_rooms:
                    keep_light_ids.update(str(lid) for lid in info.get('lights',[]))
        except Exception:
            return 0
        count=0
        try:
            for gid,info in groups.items():
                name=info.get('name','')
                if info.get('type') not in ('Room','Zone'):continue
                if name in keep_rooms:continue
                room_lights=set(str(lid) for lid in info.get('lights',[]))
                if room_lights & keep_light_ids:continue
                if info.get('state',{}).get('any_on',False):
                    if self._safe_set_group(name,'on',False):count+=1
        except Exception as e:
            self.logger.debug(f"Hue turn off error: {e}")
        return count
    def get_status(self,hide_zone_members:bool=False)->Dict:
        return {'connected':self._connected,'all_rooms':self.get_all_rooms(hide_zone_members)}
class AwayDetector:
    def __init__(self,monitor:MonitorController,delay_minutes:float=5.0,logger:Optional[logging.Logger]=None):
        self.monitor=monitor
        self.delay_minutes=max(1.0,delay_minutes)
        self._last_input_time:float=time.time()
        self._is_away=False
        self._enabled=False
        self._callback:Optional[Callable]=None
        self.logger=logger or logging.getLogger(__name__)
    def set_callback(self,callback:Callable):self._callback=callback
    def set_enabled(self,enabled:bool):
        self._enabled=enabled
        if enabled:self._last_input_time=time.time()
    def set_delay(self,minutes:float):self.delay_minutes=max(1.0,minutes)
    def is_away(self)->bool:return self._is_away
    def update_input(self):
        self._last_input_time=time.time()
        if self._is_away:
            self._is_away=False
            self.monitor.turn_on()
            if self._callback:self._callback(False)
    def check(self)->bool:
        if not self._enabled:return False
        now=time.time()
        elapsed_min=(now-self._last_input_time)/60.0
        if not self._is_away and elapsed_min>=self.delay_minutes:
            self._is_away=True
            self.monitor.turn_off()
            if self._callback:self._callback(True)
            return True
        return False
    def get_remaining_seconds(self)->float:
        if self._is_away:return 0.0
        elapsed=time.time()-self._last_input_time
        remaining=self.delay_minutes*60-elapsed
        return max(0.0,remaining)
class SleepDetector:
    def __init__(self,hue:HueController,bravia:BraviaController,monitor:MonitorController,target_room:str="Living Room",delay_minutes:float=1.0,logger:Optional[logging.Logger]=None):
        self.hue,self.bravia,self.monitor,self.target_room=hue,bravia,monitor,target_room
        self.delay_minutes=max(0.5,delay_minutes)
        self._lights_off_since:Optional[float]=None
        self._is_sleeping=False
        self._enabled=False
        self._callback:Optional[Callable]=None
        self.logger=logger or logging.getLogger(__name__)
    def set_callback(self,callback:Callable):self._callback=callback
    def set_enabled(self,enabled:bool):self._enabled=enabled
    def set_delay(self,minutes:float):self.delay_minutes=max(0.5,minutes)
    def is_sleeping(self)->bool:return self._is_sleeping
    def check(self)->bool:
        if not self._enabled:return False
        all_off=self.hue.is_all_lights_off(self.target_room)
        now=time.time()
        if all_off:
            if self._lights_off_since is None:
                self._lights_off_since=now
            elif not self._is_sleeping:
                elapsed_min=(now-self._lights_off_since)/60.0
                if elapsed_min>=self.delay_minutes:
                    self._trigger_sleep()
                    return True
        else:
            self._lights_off_since=None
        return False
    def _trigger_sleep(self):
        if self._is_sleeping:return
        self._is_sleeping=True
        self.bravia.power_off()
        self.monitor.turn_off()
        if self._callback:self._callback(True)
    def wake(self)->bool:
        if not self._is_sleeping:return False
        self._is_sleeping=False
        self._lights_off_since=None
        result=self.monitor.turn_on()
        if self._callback:self._callback(False)
        return result
class KirigamineController:
    PORT=3610
    EHD=b'\x10\x81'
    SEOJ=b'\x05\xFF\x01'
    DEOJ=b'\x01\x30\x01'
    ESV_GET=0x62;ESV_GET_RES=0x72;ESV_SETI=0x61;ESV_SET_RES=0x71;ESV_SETI_SNA=0x51
    EPC_POWER=0x80;EPC_MODE=0xB0;EPC_TEMP=0xB3;EPC_FAN=0xA0;EPC_ROOM_TEMP=0xBB
    EPC_VANE_UD_MODE=0xA1;EPC_VANE_UD_POS=0xA4;EPC_VANE_LR_MODE=0xA3;EPC_VANE_LR_POS=0xA5
    MODE_MAP={'AUTO':0x41,'COOL':0x42,'HEAT':0x43,'DRY':0x44,'FAN':0x45}
    FAN_MAP={'AUTO':0x41,'1':0x31,'2':0x32,'3':0x33,'4':0x34,'5':0x35}
    VANE_UD_POS_MAP={1:0x41,2:0x42,3:0x43,4:0x44,5:0x45}
    VANE_LR_POS_MAP={'N-LEFT':0x51,'N-CENTER':0x61,'N-RIGHT':0x60,'M-LEFT':0x52,'M-CENTER':0x54,'M-RIGHT':0x58,'W-LEFT':0x57,'W-CENTER':0x6F,'W-RIGHT':0x6C}
    _shared_lock=threading.Lock()
    def __init__(self,ip:str,port:int=3610,logger:Optional[logging.Logger]=None):
        self.ip=ip;self.port=port;self.logger=logger or logging.getLogger(__name__)
        self._tid=0;self._lock=threading.Lock()
        self._status_cache:Dict[str,Any]={};self._last_status_time:float=0;self._cache_ttl:float=2.0
        self._mode_rev={v:k for k,v in self.MODE_MAP.items()}
        self._fan_rev={v:k for k,v in self.FAN_MAP.items()}
        self._vane_lr_rev={v:k for k,v in self.VANE_LR_POS_MAP.items()}
    def _next_tid(self)->int:
        with self._lock:
            self._tid=(self._tid+1)&0xFFFF
            return self._tid
    def _build_get_packet(self,epc_list:List[int])->bytes:
        tid=self._next_tid();opc=len(epc_list)
        pkt=self.EHD+struct.pack('>H',tid)+self.SEOJ+self.DEOJ+bytes([self.ESV_GET,opc])
        for epc in epc_list:
            pkt+=bytes([epc,0])
        return pkt
    def _build_set_packet(self,props:List[tuple])->bytes:
        tid=self._next_tid();opc=len(props)
        pkt=self.EHD+struct.pack('>H',tid)+self.SEOJ+self.DEOJ+bytes([self.ESV_SETI,opc])
        for epc,val in props:
            if isinstance(val,int):
                pkt+=bytes([epc,1,val])
            elif isinstance(val,bytes):
                pkt+=bytes([epc,len(val)])+val
            else:
                pkt+=bytes([epc,1,val])
        return pkt
    def _send_receive(self,packet:bytes,expected_esv:int,timeout:float=3.0)->Optional[bytes]:
        if not self.ip:
            self.logger.warning(f"[KIRI] No IP configured")
            return None
        sock=None
        try:
            sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            sock.bind(('0.0.0.0',self.port))
            sock.settimeout(1.0)
            self.logger.info(f"[KIRI:{self.ip}] TX [{packet.hex()}]")
            sock.sendto(packet,(self.ip,self.port))
            start=time.time()
            while time.time()-start<timeout:
                try:
                    data,addr=sock.recvfrom(1024)
                    esv_str=f"ESV=0x{data[10]:02X}" if len(data)>10 else "(short)"
                    self.logger.info(f"[KIRI:{self.ip}] RX [{data.hex()}] {esv_str}")
                    if addr[0]==self.ip and len(data)>10 and data[10] in (expected_esv,self.ESV_SETI_SNA):
                        return data
                except socket.timeout:
                    continue
            self.logger.warning(f"[KIRI:{self.ip}] TIMEOUT ESV=0x{expected_esv:02X}")
            return None
        except OSError as e:
            self.logger.warning(f"[KIRI:{self.ip}] bind error: {e}")
            return None
        except Exception as e:
            self.logger.warning(f"[KIRI:{self.ip}] error: {e}")
            return None
        finally:
            if sock:sock.close()
    def _parse_response(self,data:bytes)->Dict[str,Any]:
        if not data or len(data)<12:return {}
        opc=data[11];idx=12;result:Dict[str,Any]={}
        vane_ud_mode_map={0x41:'AUTO',0x42:'MANUAL',0x43:'SWING'}
        vane_lr_mode_map={0x41:'AUTO',0x42:'MANUAL',0x43:'SWING'}
        try:
            for _ in range(opc):
                if idx>=len(data):break
                epc=data[idx];pdc=data[idx+1];idx+=2
                if pdc==0:continue
                val=data[idx:idx+pdc];idx+=pdc
                if epc==self.EPC_POWER:
                    result['power']=(val[0]==0x30)
                elif epc==self.EPC_MODE:
                    result['mode']=self._mode_rev.get(val[0],'UNKNOWN')
                elif epc==self.EPC_TEMP:
                    result['temp']=val[0]
                elif epc==self.EPC_FAN:
                    result['fan']=self._fan_rev.get(val[0],f'L{val[0]-0x30}' if 0x31<=val[0]<=0x38 else 'AUTO' if val[0]==0x41 else f'0x{val[0]:02X}')
                elif epc==self.EPC_ROOM_TEMP:
                    t=val[0];result['room_temp']=t if t<128 else t-256
                elif epc==self.EPC_VANE_UD_MODE:
                    result['vane_ud_mode']=vane_ud_mode_map.get(val[0],f'0x{val[0]:02X}')
                elif epc==self.EPC_VANE_UD_POS:
                    result['vane_ud_pos']=val[0]-0x40 if 0x41<=val[0]<=0x45 else val[0]
                elif epc==self.EPC_VANE_LR_MODE:
                    result['vane_lr_mode']=vane_lr_mode_map.get(val[0],f'0x{val[0]:02X}')
                elif epc==self.EPC_VANE_LR_POS:
                    result['vane_lr_pos']=self._vane_lr_rev.get(val[0],f'0x{val[0]:02X}')
        except Exception as e:
            self.logger.debug(f"[KIRI] parse error: {e}")
        return result
    def get_status(self)->Dict[str,Any]:
        now=time.time()
        if now-self._last_status_time<self._cache_ttl and self._status_cache:
            return self._status_cache.copy()
        with self._shared_lock:
            epc_list=[self.EPC_POWER,self.EPC_MODE,self.EPC_TEMP,self.EPC_FAN,self.EPC_ROOM_TEMP,self.EPC_VANE_UD_MODE,self.EPC_VANE_UD_POS,self.EPC_VANE_LR_MODE,self.EPC_VANE_LR_POS]
            packet=self._build_get_packet(epc_list)
            resp=self._send_receive(packet,self.ESV_GET_RES,timeout=3.0)
            if resp:
                parsed=self._parse_response(resp)
                if parsed:
                    self._status_cache.update(parsed)
                    self._last_status_time=now
                    self.logger.info(f"[KIRI] status: {parsed}")
                    return self._status_cache.copy()
            return self._status_cache.copy() if self._status_cache else {'power':None,'mode':None,'temp':None,'fan':None,'room_temp':None}
    def _disable_ai_mode(self)->bool:
        packet=self._build_set_packet([(0x8F,0x42)])
        resp=self._send_receive(packet,self.ESV_SET_RES,timeout=2.0)
        if resp and len(resp)>10 and resp[10]==self.ESV_SET_RES:
            self.logger.info(f"[KIRI:{self.ip}] OK AI_MODE_OFF")
            return True
        self.logger.warning(f"[KIRI:{self.ip}] NG AI_MODE_OFF TIMEOUT")
        return False
    def _send_props(self,props:List[tuple],desc:str="",timeout:float=3.0)->bool:
        if not props:return True
        packet=self._build_set_packet(props)
        prop_str=",".join([f"0x{p[0]:02X}=0x{p[1]:02X}" for p in props])
        resp=self._send_receive(packet,self.ESV_SET_RES,timeout=timeout)
        if resp and len(resp)>10:
            if resp[10]==self.ESV_SETI_SNA:
                self.logger.warning(f"[KIRI:{self.ip}] NG {desc}: {prop_str} REJECTED")
                return False
            self.logger.info(f"[KIRI:{self.ip}] OK {desc}: {prop_str}")
            return True
        self.logger.warning(f"[KIRI:{self.ip}] NG {desc}: {prop_str} TIMEOUT")
        return resp is not None
    def set_state(self,power:Optional[bool]=None,mode:Optional[str]=None,temp:Optional[int]=None,fan:Optional[str]=None,vane_ud:Optional[str]=None,vane_ud_pos:Optional[int]=None,vane_lr:Optional[str]=None,vane_lr_pos:Optional[str]=None)->bool:
        with self._shared_lock:
            params=[f"power={power}" if power is not None else None,f"mode={mode}" if mode else None,f"temp={temp}" if temp is not None else None,f"fan={fan}" if fan else None,f"vane_ud={vane_ud}" if vane_ud else None,f"vane_ud_pos={vane_ud_pos}" if vane_ud_pos else None,f"vane_lr={vane_lr}" if vane_lr else None]
            self.logger.info(f"[KIRI:{self.ip}] set_state: {', '.join(p for p in params if p)}")
            self._disable_ai_mode();time.sleep(0.5)
            success=True
            if power is not None:
                if not self._send_props([(self.EPC_POWER,0x30 if power else 0x31)],"power"):success=False
                time.sleep(0.5)
            if mode and mode.upper() in self.MODE_MAP:
                if not self._send_props([(self.EPC_MODE,self.MODE_MAP[mode.upper()])],"mode"):success=False
                time.sleep(0.5)
            if temp is not None:
                if not self._send_props([(self.EPC_TEMP,max(16,min(31,temp)))],"temp"):success=False
                time.sleep(0.5)
            if fan and fan.upper() in self.FAN_MAP:
                if not self._send_props([(self.EPC_FAN,self.FAN_MAP[fan.upper()])],"fan"):success=False
                time.sleep(0.5)
            if vane_ud:
                vu=vane_ud.upper()
                if vu=='SWING':
                    if not self._send_props([(self.EPC_VANE_UD_MODE,0x43)],"vane_ud=SWING"):success=False
                    time.sleep(0.5)
                elif vu=='AUTO':
                    if not self._send_props([(self.EPC_VANE_UD_MODE,0x41)],"vane_ud=AUTO"):success=False
                    time.sleep(0.5)
                elif vu in ('MANUAL','POS') and vane_ud_pos in self.VANE_UD_POS_MAP:
                    if not self._send_props([(self.EPC_VANE_UD_MODE,0x42)],"vane_ud=MANUAL"):success=False
                    time.sleep(0.5)
                    if not self._send_props([(self.EPC_VANE_UD_POS,self.VANE_UD_POS_MAP[vane_ud_pos])],f"vane_ud_pos={vane_ud_pos}"):success=False
                    time.sleep(0.5)
            if vane_lr:
                vl=vane_lr.upper()
                if vl=='SWING':
                    if not self._send_props([(self.EPC_VANE_LR_MODE,0x43)],"vane_lr=SWING"):success=False
                    time.sleep(0.5)
                elif vl in self.VANE_LR_POS_MAP:
                    if not self._send_props([(self.EPC_VANE_LR_MODE,0x42)],"vane_lr=MANUAL"):success=False
                    time.sleep(0.5)
                    if not self._send_props([(self.EPC_VANE_LR_POS,self.VANE_LR_POS_MAP[vl])],f"vane_lr_pos={vl}"):success=False
                    time.sleep(0.5)
                elif vl=='MANUAL' and vane_lr_pos and vane_lr_pos.upper() in self.VANE_LR_POS_MAP:
                    if not self._send_props([(self.EPC_VANE_LR_MODE,0x42)],"vane_lr=MANUAL"):success=False
                    time.sleep(0.5)
                    if not self._send_props([(self.EPC_VANE_LR_POS,self.VANE_LR_POS_MAP[vane_lr_pos.upper()])],f"vane_lr_pos={vane_lr_pos}"):success=False
                    time.sleep(0.5)
            self._last_status_time=0
            self.logger.info(f"[KIRI:{self.ip}] RESULT: {'SUCCESS' if success else 'FAILED'}")
            return success
    def set_state_with_retry(self,max_retries:int=5,interval:float=3.0,**kwargs)->bool:
        for i in range(max_retries):
            if self.set_state(**kwargs):
                self.logger.debug(f"[KIRI] set_state succeeded at attempt {i+1}")
                return True
            time.sleep(interval)
        self.logger.warning(f"[KIRI] set_state failed after {max_retries} retries")
        return False
    def power_on(self)->bool:return self.set_state(power=True)
    def power_off(self)->bool:return self.set_state(power=False)
    def set_cooling(self,temp:int=26)->bool:return self.set_state(power=True,mode='COOL',temp=temp)
    def set_heating(self,temp:int=22)->bool:return self.set_state(power=True,mode='HEAT',temp=temp)
class SwitchbotController:
    API_BASE="https://api.switch-bot.com/v1.1"
    API_TIMEOUT=5.0
    BACKOFF_TTL=900.0
    SENSOR_TYPES={'Meter','MeterPlus','MeterPro','MeterPro(CO2)','WoIOSensor','Hub 2'}
    BOT_TYPES={'Bot','Plug','Plug Mini (US)','Plug Mini (JP)'}
    def __init__(self,token:str='',device_id:str='',logger:Optional[logging.Logger]=None):
        self.logger=logger or logging.getLogger(__name__)
        self._token=token
        self._device_id=device_id
        self._status_cache:Dict={'temperature':None,'humidity':None,'battery':None}
        self._last_fetch:float=0.0
        self._cache_ttl:float=60.0
        self._backoff_until:float=0.0
    def set_credentials(self,token:str,device_id:str):
        self._token=token
        self._device_id=device_id
        self._status_cache={'temperature':None,'humidity':None,'battery':None,'co2':None}
        self._backoff_until=0.0
    def is_configured(self)->bool:return bool(self._token and self._device_id)
    @staticmethod
    def fetch_devices(token:str)->Tuple[List[Dict],List[Dict]]:
        if not REQUESTS_AVAILABLE:raise RuntimeError("requests module not available")
        if not token:raise ValueError("SwitchBot token is empty")
        headers={"Authorization":token,"Content-Type":"application/json;charset=utf8"}
        resp=requests.get(f"{SwitchbotController.API_BASE}/devices",headers=headers,timeout=SwitchbotController.API_TIMEOUT)
        if resp.status_code!=200:raise RuntimeError(f"API error: HTTP {resp.status_code}")
        data=resp.json()
        if data.get('statusCode')!=100:raise RuntimeError(f"API error: {data.get('message','unknown')}")
        body=data.get('body',{})
        return body.get('deviceList',[]),body.get('infraredRemoteList',[])
    @staticmethod
    def send_command(token:str,device_id:str,command:str,param:str='default')->bool:
        if not token or not device_id:return False
        try:
            headers={"Authorization":token,"Content-Type":"application/json;charset=utf8"}
            body={"command":command,"parameter":param,"commandType":"command"}
            resp=requests.post(f"{SwitchbotController.API_BASE}/devices/{device_id}/commands",headers=headers,json=body,timeout=SwitchbotController.API_TIMEOUT)
            return resp.status_code==200 and resp.json().get('statusCode')==100
        except Exception:
            return False
    @staticmethod
    def send_ir_command(token:str,device_id:str,button_name:str,logger=None)->bool:
        if not token or not device_id:return False
        try:
            headers={"Authorization":token,"Content-Type":"application/json;charset=utf8"}
            body={"command":button_name,"parameter":"default","commandType":"customize"}
            resp=requests.post(f"{SwitchbotController.API_BASE}/devices/{device_id}/commands",headers=headers,json=body,timeout=SwitchbotController.API_TIMEOUT)
            result=resp.json()
            if logger:logger.info(f"IR command: device={device_id}, button={button_name}, status={result.get('statusCode')}, msg={result.get('message')}")
            return resp.status_code==200 and result.get('statusCode')==100
        except Exception as e:
            if logger:logger.error(f"IR command failed: {e}")
            return False
    def get_status(self,force:bool=False)->Dict:
        if not self.is_configured():return self._status_cache
        now=time.time()
        if now<self._backoff_until:return self._status_cache
        if not force and (now-self._last_fetch)<self._cache_ttl and self._last_fetch>0:
            return self._status_cache
        if not REQUESTS_AVAILABLE:return self._status_cache
        try:
            headers={"Authorization":self._token,"Content-Type":"application/json;charset=utf8"}
            resp=requests.get(f"{self.API_BASE}/devices/{self._device_id}/status",headers=headers,timeout=self.API_TIMEOUT)
            if resp.status_code==429:
                self._backoff_until=now+self.BACKOFF_TTL
                self._last_fetch=now
                return self._status_cache
            if resp.status_code==200:
                data=resp.json()
                if data.get('statusCode')==100:
                    body=data.get('body',{})
                    self._status_cache={'temperature':body.get('temperature'),'humidity':body.get('humidity'),'battery':body.get('battery'),'co2':body.get('CO2')}
                    self._last_fetch=now
                    self._backoff_until=0.0
        except Exception:
            self._last_fetch=now
        return self._status_cache
    def get_temperature(self)->Optional[float]:return self.get_status().get('temperature')
    def get_humidity(self)->Optional[int]:return self.get_status().get('humidity')
    def get_co2(self)->Optional[int]:return self.get_status().get('co2')
    def set_cache(self,cache:Dict):
        if cache:self._status_cache.update({k:v for k,v in cache.items() if v is not None})
    @staticmethod
    def get_cache_path()->Path:return Path(__file__).parent.parent/'logs'/'switchbot_cache.json'
    @staticmethod
    def load_all_cache()->Dict[str,Dict]:
        p=SwitchbotController.get_cache_path()
        if not p.exists():return {}
        try:return json.loads(p.read_text(encoding='utf-8'))
        except Exception:return {}
    @staticmethod
    def save_all_cache(cache:Dict[str,Dict]):
        p=SwitchbotController.get_cache_path()
        try:p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(cache,ensure_ascii=False),encoding='utf-8')
        except Exception:pass
class DesktopOrganizer:
    SCAN_INTERVAL=10.0
    ICON_SPACING_X=90
    ICON_SPACING_Y=80
    MARGIN_X=20
    MARGIN_Y=20
    def __init__(self,layout_path:str,logger:Optional[logging.Logger]=None,custom_desktop_path:Optional[str]=None):
        self._is_windows=sys.platform=='win32'
        self.logger=logger or logging.getLogger(__name__)
        self.layout_path=layout_path
        self._layout:Dict[str,tuple]={}
        self._running=False
        self._thread:Optional[threading.Thread]=None
        self._desktop_path:Optional[str]=None
        self._custom_desktop_path=custom_desktop_path
        self._screen_left=0
        self._screen_top=0
        self._screen_width=1920
        self._screen_height=1080
        self._max_cols=10
        self._enabled=False
        if self._is_windows:
            try:
                import ctypes
                from ctypes import wintypes
                class RECT(ctypes.Structure):
                    _fields_=[('left',ctypes.c_long),('top',ctypes.c_long),('right',ctypes.c_long),('bottom',ctypes.c_long)]
                class MONITORINFO(ctypes.Structure):
                    _fields_=[('cbSize',wintypes.DWORD),('rcMonitor',RECT),('rcWork',RECT),('dwFlags',wintypes.DWORD)]
                class POINT(ctypes.Structure):
                    _fields_=[('x',ctypes.c_long),('y',ctypes.c_long)]
                user32=ctypes.windll.user32
                hMonitor=user32.MonitorFromPoint(POINT(0,0),1)
                mi=MONITORINFO()
                mi.cbSize=ctypes.sizeof(MONITORINFO)
                if user32.GetMonitorInfoW(hMonitor,ctypes.byref(mi)):
                    self._screen_left=mi.rcWork.left
                    self._screen_top=mi.rcWork.top
                    self._screen_width=mi.rcWork.right-mi.rcWork.left
                    self._screen_height=mi.rcWork.bottom-mi.rcWork.top
                self._max_cols=max(1,(self._screen_width-self.MARGIN_X*2)//self.ICON_SPACING_X)
            except Exception as e:
                pass
        self._load_layout()
    def set_desktop_path(self,path:str)->bool:
        import os
        if path and os.path.isdir(path):
            self._custom_desktop_path=path
            self._desktop_path=path
            return True
        return False
    def _get_desktop_path(self)->Optional[str]:
        if self._desktop_path: return self._desktop_path
        import os
        if self._custom_desktop_path and os.path.isdir(self._custom_desktop_path):
            self._desktop_path=self._custom_desktop_path
            return self._desktop_path
        if not self._is_windows: return None
        candidates=[os.path.join(os.environ.get('USERPROFILE',''),'Desktop'),os.path.join(os.environ.get('USERPROFILE',''),'OneDrive','Desktop'),os.path.join(os.environ.get('USERPROFILE',''),'OneDrive','デスクトップ'),os.path.join(os.path.expanduser('~'),'Desktop')]
        for path in candidates:
            if path and os.path.isdir(path):
                self._desktop_path=path
                return self._desktop_path
        return None
    def _load_layout(self):
        try:
            import os
            if os.path.exists(self.layout_path):
                with open(self.layout_path,'r',encoding='utf-8') as f:
                    data=json.load(f)
                    self._layout={k:tuple(v) for k,v in data.get('icons',{}).items()}
        except Exception as e:
            self.logger.error(f"[DESKTOP] Load layout error: {e}")
    def _save_layout(self):
        try:
            with open(self.layout_path,'w',encoding='utf-8') as f:
                json.dump({'icons':self._layout,'updated':time.strftime('%Y-%m-%d %H:%M:%S')},f,ensure_ascii=False,indent=2)
        except Exception as e:
            self.logger.error(f"[DESKTOP] Save layout error: {e}")
    def _get_listview_handle(self)->Optional[int]:
        if not self._is_windows:
            return None
        try:
            import ctypes
            user32=ctypes.windll.user32
            progman=user32.FindWindowW('Progman',None)
            defview=user32.FindWindowExW(progman,None,'SHELLDLL_DefView',None)
            if not defview:
                def enum_callback(hwnd,lparam):
                    if user32.FindWindowExW(hwnd,None,'SHELLDLL_DefView',None):
                        ctypes.cast(lparam,ctypes.POINTER(ctypes.c_void_p))[0]=hwnd
                        return False
                    return True
                WNDENUMPROC=ctypes.WINFUNCTYPE(ctypes.c_bool,ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p))
                result=ctypes.c_void_p()
                user32.EnumWindows(WNDENUMPROC(enum_callback),ctypes.byref(result))
                if result.value:
                    defview=user32.FindWindowExW(result.value,None,'SHELLDLL_DefView',None)
            if defview:
                listview=user32.FindWindowExW(defview,None,'SysListView32',None)
                return listview
        except Exception:
            pass
        return None
    def _get_icon_positions(self)->Dict[str,tuple]:
        result={}
        desktop=self._get_desktop_path()
        if not desktop: return result
        import os
        try: files=[f for f in os.listdir(desktop) if not f.startswith('.')]
        except: return result
        def _grid_fallback():
            fb={}
            max_rows=max(1,(self._screen_height-self.MARGIN_Y*2)//self.ICON_SPACING_Y)
            for i,f in enumerate(sorted(files)):
                col,row=i%self._max_cols,i//self._max_cols
                if row>=max_rows: break
                fb[f]=(self._screen_left+self.MARGIN_X+col*self.ICON_SPACING_X,self._screen_top+self.MARGIN_Y+row*self.ICON_SPACING_Y)
            return fb
        if not self._is_windows: return _grid_fallback()
        try:
            import ctypes
            from ctypes import wintypes
            listview=self._get_listview_handle()
            if not listview:
                return _grid_fallback()
            user32=ctypes.windll.user32
            kernel32=ctypes.windll.kernel32
            LVM_GETITEMCOUNT=0x1004
            LVM_GETITEMPOSITION=0x1010
            LVM_GETITEMTEXTW=0x1073
            count=user32.SendMessageW(listview,LVM_GETITEMCOUNT,0,0)
            pid=wintypes.DWORD()
            user32.GetWindowThreadProcessId(listview,ctypes.byref(pid))
            PROCESS_VM_OPERATION=0x0008
            PROCESS_VM_READ=0x0010
            PROCESS_VM_WRITE=0x0020
            hproc=kernel32.OpenProcess(PROCESS_VM_OPERATION|PROCESS_VM_READ|PROCESS_VM_WRITE,False,pid.value)
            if not hproc:
                return _grid_fallback()
            MEM_COMMIT=0x1000
            MEM_RELEASE=0x8000
            PAGE_READWRITE=0x04
            class POINT(ctypes.Structure):
                _fields_=[('x',ctypes.c_long),('y',ctypes.c_long)]
            class LVITEMW(ctypes.Structure):
                _fields_=[('mask',ctypes.c_uint),('iItem',ctypes.c_int),('iSubItem',ctypes.c_int),('state',ctypes.c_uint),('stateMask',ctypes.c_uint),('pszText',ctypes.c_void_p),('cchTextMax',ctypes.c_int),('iImage',ctypes.c_int),('lParam',ctypes.POINTER(ctypes.c_long)),('iIndent',ctypes.c_int)]
            buf_size=520
            remote_buf=kernel32.VirtualAllocEx(hproc,None,buf_size,MEM_COMMIT,PAGE_READWRITE)
            if not remote_buf:
                kernel32.CloseHandle(hproc)
                return _grid_fallback()
            for i in range(count):
                pt=POINT()
                kernel32.WriteProcessMemory(hproc,remote_buf,ctypes.byref(pt),ctypes.sizeof(pt),None)
                user32.SendMessageW(listview,LVM_GETITEMPOSITION,i,remote_buf)
                kernel32.ReadProcessMemory(hproc,remote_buf,ctypes.byref(pt),ctypes.sizeof(pt),None)
                lvi=LVITEMW()
                lvi.mask=0x0001
                lvi.iItem=i
                lvi.iSubItem=0
                lvi.cchTextMax=260
                lvi.pszText=remote_buf+ctypes.sizeof(LVITEMW)
                kernel32.WriteProcessMemory(hproc,remote_buf,ctypes.byref(lvi),ctypes.sizeof(lvi),None)
                user32.SendMessageW(listview,LVM_GETITEMTEXTW,i,remote_buf)
                text_buf=(ctypes.c_wchar*260)()
                kernel32.ReadProcessMemory(hproc,remote_buf+ctypes.sizeof(LVITEMW),text_buf,520,None)
                name=text_buf.value
                if name: result[name]=(pt.x,pt.y)
            kernel32.VirtualFreeEx(hproc,remote_buf,0,MEM_RELEASE)
            kernel32.CloseHandle(hproc)
        except Exception as e:
            return _grid_fallback()
        if not result and files:
            return _grid_fallback()
        return result
    def _set_icon_position(self,name:str,x:int,y:int)->bool:
        if not self._is_windows:
            return False
        try:
            import ctypes
            from ctypes import wintypes
            listview=self._get_listview_handle()
            if not listview:
                return False
            user32=ctypes.windll.user32
            kernel32=ctypes.windll.kernel32
            LVM_GETITEMCOUNT=0x1004
            LVM_GETITEMTEXTW=0x1073
            LVM_SETITEMPOSITION=0x100F
            count=user32.SendMessageW(listview,LVM_GETITEMCOUNT,0,0)
            pid=wintypes.DWORD()
            user32.GetWindowThreadProcessId(listview,ctypes.byref(pid))
            hproc=kernel32.OpenProcess(0x0038,False,pid.value)
            if not hproc:
                return False
            class LVITEMW(ctypes.Structure):
                _fields_=[('mask',ctypes.c_uint),('iItem',ctypes.c_int),('iSubItem',ctypes.c_int),('state',ctypes.c_uint),('stateMask',ctypes.c_uint),('pszText',ctypes.c_void_p),('cchTextMax',ctypes.c_int),('iImage',ctypes.c_int),('lParam',ctypes.POINTER(ctypes.c_long)),('iIndent',ctypes.c_int)]
            remote_buf=kernel32.VirtualAllocEx(hproc,None,520,0x1000,0x04)
            if not remote_buf:
                kernel32.CloseHandle(hproc)
                return False
            target_idx=-1
            for i in range(count):
                lvi=LVITEMW()
                lvi.mask=0x0001
                lvi.iItem=i
                lvi.iSubItem=0
                lvi.cchTextMax=260
                lvi.pszText=remote_buf+ctypes.sizeof(LVITEMW)
                kernel32.WriteProcessMemory(hproc,remote_buf,ctypes.byref(lvi),ctypes.sizeof(lvi),None)
                user32.SendMessageW(listview,LVM_GETITEMTEXTW,i,remote_buf)
                text_buf=(ctypes.c_wchar*260)()
                kernel32.ReadProcessMemory(hproc,remote_buf+ctypes.sizeof(LVITEMW),text_buf,520,None)
                if text_buf.value==name:
                    target_idx=i
                    break
            kernel32.VirtualFreeEx(hproc,remote_buf,0,0x8000)
            kernel32.CloseHandle(hproc)
            if target_idx>=0:
                pos=(y<<16)|(x&0xFFFF)
                user32.SendMessageW(listview,LVM_SETITEMPOSITION,target_idx,pos)
                return True
        except Exception:
            pass
        return False
    def _is_in_primary_monitor(self,x:int,y:int)->bool:
        return (self._screen_left<=x<self._screen_left+self._screen_width and self._screen_top<=y<self._screen_top+self._screen_height)
    def _calc_next_position(self)->tuple:
        max_rows=max(1,(self._screen_height-self.MARGIN_Y*2)//self.ICON_SPACING_Y)
        if not self._layout:
            return (self._screen_left+self.MARGIN_X,self._screen_top+self.MARGIN_Y)
        max_row=0
        last_col=0
        for name,(x,y) in self._layout.items():
            if not self._is_in_primary_monitor(x,y): continue
            row=max(0,(y-self._screen_top-self.MARGIN_Y)//self.ICON_SPACING_Y)
            col=max(0,(x-self._screen_left-self.MARGIN_X)//self.ICON_SPACING_X)
            if row>max_row or (row==max_row and col>last_col):
                max_row=row
                last_col=col
        next_col=last_col+1
        next_row=max_row
        if next_col>=self._max_cols:
            next_col=0
            next_row+=1
        if next_row>=max_rows:
            next_row=0
            next_col=0
        return (self._screen_left+self.MARGIN_X+next_col*self.ICON_SPACING_X,self._screen_top+self.MARGIN_Y+next_row*self.ICON_SPACING_Y)
    def _refresh_desktop(self)->bool:
        if not self._is_windows:
            return False
        try:
            import ctypes
            SHCNE_ASSOCCHANGED=0x08000000
            SHCNF_IDLIST=0x0000
            ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED,SHCNF_IDLIST,None,None)
            return True
        except Exception:
            pass
        return False
    def scan_and_organize(self)->Dict[str,Any]:
        result={'refreshed':False}
        current_positions=self._get_icon_positions()
        current_count=len(current_positions)
        if not hasattr(self,'_last_icon_count'):
            self._last_icon_count=current_count
            return result
        if current_count>self._last_icon_count:
            self._refresh_desktop()
            result['refreshed']=True
        self._last_icon_count=current_count
        return result
    def initialize_layout(self)->int:
        positions=self._get_icon_positions()
        self._layout={}
        relocated=0
        for name,(x,y) in positions.items():
            if self._is_in_primary_monitor(x,y):
                self._layout[name]=(x,y)
            else:
                new_pos=self._calc_next_position()
                self._layout[name]=new_pos
                self._set_icon_position(name,new_pos[0],new_pos[1])
                relocated+=1
        self._save_layout()
        return len(self._layout)
    def _loop(self):
        while self._running:
            if self._enabled:
                try:
                    self.scan_and_organize()
                except Exception as e:
                    print(f"[DESKTOP] Loop error: {e}")
            time.sleep(self.SCAN_INTERVAL)
    def start(self)->bool:
        if self._running:
            return True
        self._running=True
        self._thread=threading.Thread(target=self._loop,daemon=True,name="DesktopOrganizer")
        self._thread.start()
        return True
    def stop(self):
        self._running=False
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread=None
    def set_enabled(self,enabled:bool):
        if enabled and not self._layout: self.initialize_layout()
        self._enabled=enabled
    def is_enabled(self)->bool:
        return self._enabled
    def is_running(self)->bool:
        return self._running
class AmbientSync:
    POLL_INTERVAL=3.0
    ACTIVITY_TIMEOUT=5.0
    API_TIMEOUT=2.0
    HEALTH_CHECK_INTERVAL=15.0
    RECONNECT_GRACE_SECONDS=5.0
    MANUAL_OVERRIDE_SECONDS=300.0
    DEFAULT_THRESHOLDS={'off':50,'high':5,'low':1}
    DEFAULT_VOLUME_PROFILES={'Spotify':{'enabled':False,'volume':15},'Netflix':{'enabled':False,'volume':20},'YouTube':{'enabled':False,'volume':20},'Prime Video':{'enabled':False,'volume':20}}
    SCREENSAVER_APPS={'Dreamx','Daydream','Screensaver'}
    def __init__(self,config:Dict,logger:Optional[logging.Logger]=None):
        self.config=config
        self.logger=logger or logging.getLogger(__name__)
        self.hue=HueController(config.get('hue_ip',''),self.logger)
        self.bravia=BraviaController(config.get('bravia_ip',''),config.get('bravia_psk',''),self.logger)
        self.kirigamine=KirigamineController(config.get('kirigamine_ip',''),logger=self.logger)
        self.kirigamine_bedroom=KirigamineController(config.get('kirigamine_bedroom_ip',''),logger=self.logger)
        sb_token=config.get('switchbot_token','')
        sb_devices=config.get('switchbot_devices',{})
        self.switchbot_controllers:Dict[str,SwitchbotController]={}
        for name,info in sb_devices.items():
            dev_id=info.get('id') if isinstance(info,dict) else info
            if dev_id:self.switchbot_controllers[name]=SwitchbotController(sb_token,dev_id,self.logger)
        self.switchbot_living=self.switchbot_controllers.get('living') or SwitchbotController(sb_token,'',self.logger)
        self.switchbot_bedroom=self.switchbot_controllers.get('bedroom') or SwitchbotController(sb_token,'',self.logger)
        self.switchbot_co2=self.switchbot_controllers.get('co2') or SwitchbotController(sb_token,'',self.logger)
        sb_cache=SwitchbotController.load_all_cache()
        for name,ctrl in self.switchbot_controllers.items():
            if name in sb_cache:ctrl.set_cache(sb_cache[name])
        self.monitor=MonitorController(self.logger)
        self.away_detector=AwayDetector(self.monitor,config.get('away_detection_minutes',5.0),self.logger)
        self._sleep_detection_room=config.get('sleep_detection_room','')
        self.sleep_detector=SleepDetector(self.hue,self.bravia,self.monitor,self._sleep_detection_room,config.get('sleep_detection_minutes',1.0),self.logger)
        self._brightness_sync_enabled=config.get('brightness_sync_enabled',config.get('auto_start',False))
        self._volume_auto_enabled=config.get('volume_auto_enabled',False)
        self._running=False
        self._thread:Optional[threading.Thread]=None
        self._executor:Optional[ThreadPoolExecutor]=None
        self._last_mode:Optional[PowerSavingMode]=None
        self._last_app:Optional[str]=None
        self._status_callback:Optional[Callable]=None
        self._sleep_callback:Optional[Callable]=None
        self._away_callback:Optional[Callable]=None
        self._hue_status:Dict={}
        self._bravia_status:Dict={}
        self._kirigamine_status:Dict={}
        self._kirigamine_bedroom_status:Dict={}
        self._switchbot_status:Dict[str,Dict]={}
        self._switchbot_living_status:Dict=sb_cache.get('living',{}).copy()
        self._switchbot_bedroom_status:Dict=sb_cache.get('bedroom',{}).copy()
        if 'co2' in sb_cache and sb_cache['co2'].get('co2'):self._switchbot_living_status['co2']=sb_cache['co2']['co2']
        self._thresholds=config.get('thresholds',self.DEFAULT_THRESHOLDS.copy())
        self._volume_profiles=config.get('volume_profiles',self.DEFAULT_VOLUME_PROFILES.copy())
        self._last_input_time:float=time.time()
        self._focus_lighting_enabled=config.get('focus_lighting',False)
        self._focus_keep_rooms:List[str]=config.get('focus_keep_rooms',[])
        self._hide_zone_members:bool=config.get('hide_zone_members',False)
        self._sleep_detection_enabled=config.get('sleep_detection_enabled',False)
        self._away_detection_enabled=config.get('away_detection_enabled',False)
        self.sleep_detector.set_enabled(self._sleep_detection_enabled)
        self.away_detector.set_enabled(self._away_detection_enabled)
        self._last_known_volume:Optional[int]=None
        self._volume_override_until:float=0.0
        self._reconnect_grace_until:float=0.0
        self._last_bravia_health_check:float=0.0
        self._co2_history:List[Tuple[float,int]]=[]
        self._co2_last_action_time:float=0.0
        self._co2_current_level:str='normal'
        self._co2_automation_enabled:bool=config.get('co2_automation_enabled',False)
        self._co2_rules:List[Dict]=config.get('co2_rules',[{'threshold':1200,'fan':'High','vent':'high'},{'threshold':900,'fan':'Med','vent':'low'},{'threshold':700,'fan':'Low','vent':'off','below':True}])
        self._co2_dwell_minutes:float=config.get('co2_dwell_minutes',3.0)
        self._co2_cooldown_minutes:float=config.get('co2_cooldown_minutes',5.0)
        sb_devices=config.get('switchbot_devices',{})
        self._fan_device_id=sb_devices.get('fan',{}).get('id','') if isinstance(sb_devices.get('fan'),dict) else ''
        self._vent_high_id=sb_devices.get('vent_high',{}).get('id','') if isinstance(sb_devices.get('vent_high'),dict) else ''
        self._vent_low_id=sb_devices.get('vent_low',{}).get('id','') if isinstance(sb_devices.get('vent_low'),dict) else ''
        self._vent_off_id=sb_devices.get('vent_off',{}).get('id','') if isinstance(sb_devices.get('vent_off'),dict) else ''
    def set_status_callback(self,callback:Callable):self._status_callback=callback
    def set_sleep_callback(self,callback:Callable):
        self._sleep_callback=callback
        self.sleep_detector.set_callback(callback)
    def set_thresholds(self,off:int,low:int,high:int):
        self._thresholds={'off':off,'low':low,'high':high}
        self.config['thresholds']=self._thresholds
    def get_thresholds(self)->Dict:return self._thresholds
    def set_volume_profiles(self,profiles:Dict):
        self._volume_profiles=profiles
        self.config['volume_profiles']=profiles
    def update_switchbot_config(self,devices:Dict[str,Dict]):
        sb_token=self.config.get('switchbot_token','')
        for key,info in devices.items():
            dev_id=info.get('id') if isinstance(info,dict) else ''
            if dev_id:
                if key not in self.switchbot_controllers:
                    self.switchbot_controllers[key]=SwitchbotController(sb_token,dev_id,self.logger)
                else:
                    self.switchbot_controllers[key].set_credentials(sb_token,dev_id)
        self.switchbot_living=self.switchbot_controllers.get('living') or SwitchbotController(sb_token,'',self.logger)
        self.switchbot_bedroom=self.switchbot_controllers.get('bedroom') or SwitchbotController(sb_token,'',self.logger)
        self.switchbot_co2=self.switchbot_controllers.get('co2') or SwitchbotController(sb_token,'',self.logger)
        self._fan_device_id=devices.get('fan',{}).get('id','') if isinstance(devices.get('fan'),dict) else ''
        self._vent_high_id=devices.get('vent_high',{}).get('id','') if isinstance(devices.get('vent_high'),dict) else ''
        self._vent_low_id=devices.get('vent_low',{}).get('id','') if isinstance(devices.get('vent_low'),dict) else ''
        self._vent_off_id=devices.get('vent_off',{}).get('id','') if isinstance(devices.get('vent_off'),dict) else ''
        self.config['switchbot_devices']=devices
    def update_co2_config(self,home_cfg:Dict):
        self._co2_automation_enabled=home_cfg.get('co2_automation_enabled',False)
        self._co2_rules=home_cfg.get('co2_rules',self._co2_rules)
        self._co2_dwell_minutes=home_cfg.get('co2_dwell_minutes',3.0)
        self._co2_cooldown_minutes=home_cfg.get('co2_cooldown_minutes',5.0)
    def get_volume_profiles(self)->Dict:return self._volume_profiles
    def update_user_activity(self,is_active:bool):
        if is_active:
            self._last_input_time=time.time()
            self.away_detector.update_input()
    def set_away_callback(self,callback:Callable):
        self._away_callback=callback
        self.away_detector.set_callback(callback)
    def set_away_detection(self,enabled:bool,delay_minutes:float=None):
        self._away_detection_enabled=enabled
        self.away_detector.set_enabled(enabled)
        if delay_minutes is not None:
            self.away_detector.set_delay(delay_minutes)
            self.config['away_detection_minutes']=delay_minutes
        self.config['away_detection_enabled']=enabled
    def is_away(self)->bool:return self.away_detector.is_away()
    def get_away_remaining(self)->float:return self.away_detector.get_remaining_seconds()
    def set_focus_lighting(self,enabled:bool,keep_rooms:List[str]=None):
        self._focus_lighting_enabled=enabled
        self.config['focus_lighting']=enabled
        if keep_rooms is not None:
            self._focus_keep_rooms=keep_rooms
            self.config['focus_keep_rooms']=keep_rooms
    def get_focus_keep_rooms(self)->List[str]:return self._focus_keep_rooms.copy()
    def set_hide_zone_members(self,enabled:bool):
        self._hide_zone_members=enabled
        self.config['hide_zone_members']=enabled
    def get_hide_zone_members(self)->bool:return self._hide_zone_members
    def set_sleep_detection(self,enabled:bool,delay_minutes:float=None):
        self._sleep_detection_enabled=enabled
        self.sleep_detector.set_enabled(enabled)
        if delay_minutes is not None:
            self.sleep_detector.set_delay(delay_minutes)
            self.config['sleep_detection_minutes']=delay_minutes
        self.config['sleep_detection_enabled']=enabled
    def wake_monitors(self)->bool:return self.sleep_detector.wake()
    def is_sleeping(self)->bool:return self.sleep_detector.is_sleeping()
    def set_co2_automation(self,enabled:bool,thresholds:Dict=None,dwell:float=None,cooldown:float=None):
        self._co2_automation_enabled=enabled
        self.config['co2_automation_enabled']=enabled
        if thresholds:
            self._co2_thresholds=thresholds
            self.config['co2_thresholds']=thresholds
        if dwell is not None:
            self._co2_dwell_minutes=dwell
            self.config['co2_dwell_minutes']=dwell
        if cooldown is not None:
            self._co2_cooldown_minutes=cooldown
            self.config['co2_cooldown_minutes']=cooldown
    def _check_co2_automation(self,co2:int):
        if not self._co2_automation_enabled:return
        now=time.time()
        self._co2_history.append((now,co2))
        cutoff=now-self._co2_dwell_minutes*60
        self._co2_history=[x for x in self._co2_history if x[0]>=cutoff]
        if len(self._co2_history)<2:return
        if now-self._co2_last_action_time<self._co2_cooldown_minutes*60:return
        avg_co2=sum(x[1] for x in self._co2_history)/len(self._co2_history)
        min_co2=min(x[1] for x in self._co2_history)
        max_co2=max(x[1] for x in self._co2_history)
        if max_co2-min_co2>200:return
        matched_rule=None
        for rule in self._co2_rules:
            thresh=rule.get('threshold',800)
            if rule.get('below'):
                if avg_co2<thresh:matched_rule=rule;break
            else:
                if avg_co2>thresh:matched_rule=rule;break
        if matched_rule:
            rule_key=f"{matched_rule.get('fan','Off')}_{matched_rule.get('vent','off')}"
            if rule_key!=self._co2_current_level:
                self._apply_co2_rule(matched_rule)
                self._co2_current_level=rule_key
                self._co2_last_action_time=now
    def _apply_co2_rule(self,rule:Dict):
        token=self.config.get('switchbot_token','')
        if not token:return
        fan_level=rule.get('fan','Low')
        vent_level=rule.get('vent','off')
        if self._fan_device_id:
            SwitchbotController.send_ir_command(token,self._fan_device_id,fan_level,self.logger)
            self.logger.info(f"CO2 automation: fan IR -> {fan_level}")
        vent_id_map={'high':self._vent_high_id,'low':self._vent_low_id,'off':self._vent_off_id}
        vent_id=vent_id_map.get(vent_level,'')
        if vent_id:
            SwitchbotController.send_command(token,vent_id,'press')
            self.logger.info(f"CO2 automation: vent Bot -> {vent_level}")
    def _is_user_active(self)->bool:
        return (time.time()-self._last_input_time)<self.ACTIVITY_TIMEOUT
    def _apply_focus_lighting(self):
        if not self._focus_lighting_enabled:return
        if not self._is_user_active():return
        if self._focus_keep_rooms:self.hue.turn_off_except_rooms(self._focus_keep_rooms)
    def _brightness_to_mode(self,brightness:float)->PowerSavingMode:
        pct=brightness*100
        if pct>self._thresholds.get('off',50):return PowerSavingMode.OFF
        elif pct<self._thresholds.get('high',5):return PowerSavingMode.HIGH
        else:return PowerSavingMode.LOW
    def _on_bravia_reconnect(self)->None:
        self._reconnect_grace_until=time.time()+self.RECONNECT_GRACE_SECONDS
        self._last_app=None
    def _detect_manual_volume_change(self,current_vol:int,current_app:str)->bool:
        if self._last_known_volume is None:
            return False
        if current_app==self._last_app and current_vol!=self._last_known_volume:
            self._volume_override_until=time.time()+self.MANUAL_OVERRIDE_SECONDS
            return True
        return False
    def _check_app_volume(self,current_app:str)->None:
        now=time.time()
        current_vol=self._bravia_status.get('volume')
        if now<self._reconnect_grace_until:
            self._last_known_volume=current_vol
            self._last_app=current_app
            return
        if self._detect_manual_volume_change(current_vol,current_app):
            self._last_known_volume=current_vol
            self._last_app=current_app
            return
        if now<self._volume_override_until:
            self._last_known_volume=current_vol
            self._last_app=current_app
            return
        if current_app==self._last_app:
            self._last_known_volume=current_vol
            return
        if self._last_app in self.SCREENSAVER_APPS:
            self._last_app=current_app
            self._last_known_volume=current_vol
            return
        if current_app and current_app!="Unknown":
            profile=self._volume_profiles.get(current_app)
            if profile and profile.get('enabled',False):
                target_vol=profile.get('volume',20)
                if self.bravia.set_volume(target_vol):
                    self._last_known_volume=target_vol
        self._last_app=current_app
    def _fetch_hue_status(self)->Dict:
        try:
            return self.hue.get_status(self._hide_zone_members)
        except Exception as e:
            self.logger.debug(f"Hue status error: {e}")
            return {}
    def _fetch_bravia_status(self)->Dict:
        try:
            return self.bravia.get_status()
        except Exception as e:
            self.logger.debug(f"Bravia status error: {e}")
            return {}
    def _fetch_kirigamine_status(self)->Dict:
        try:
            return self.kirigamine.get_status()
        except Exception as e:
            self.logger.debug(f"Kirigamine status error: {e}")
            return {}
    def _fetch_kirigamine_bedroom_status(self)->Dict:
        try:
            return self.kirigamine_bedroom.get_status()
        except Exception as e:
            self.logger.debug(f"Kirigamine bedroom status error: {e}")
            return {}
    def _fetch_switchbot_status(self)->Dict:
        try:
            return self.switchbot_living.get_status()
        except Exception as e:
            self.logger.debug(f"Switchbot living status error: {e}")
            return {}
    def _fetch_switchbot_bedroom_status(self)->Dict:
        try:
            return self.switchbot_bedroom.get_status()
        except Exception as e:
            self.logger.debug(f"Switchbot bedroom status error: {e}")
            return {}
    def _sync_loop(self):
        while self._running:
            try:
                now=time.time()
                if now-self._last_bravia_health_check>self.HEALTH_CHECK_INTERVAL:
                    if not self.bravia.is_healthy():
                        self.logger.warning("BRAVIA unhealthy - attempting reconnect")
                        self.bravia.ensure_connection('periodic')
                    self._last_bravia_health_check=now
                hue_future=self._executor.submit(self._fetch_hue_status)
                bravia_future=self._executor.submit(self._fetch_bravia_status)
                kiri_future=self._executor.submit(self._fetch_kirigamine_status)
                kiri_bed_future=self._executor.submit(self._fetch_kirigamine_bedroom_status)
                sb_living_future=self._executor.submit(self._fetch_switchbot_status) if self.switchbot_living.is_configured() else None
                sb_bedroom_future=self._executor.submit(self._fetch_switchbot_bedroom_status) if self.switchbot_bedroom.is_configured() else None
                try:
                    self._hue_status=hue_future.result(timeout=self.API_TIMEOUT)
                except FuturesTimeoutError:
                    self.logger.debug("Hue status timeout")
                try:
                    new_bravia=bravia_future.result(timeout=self.API_TIMEOUT)
                    if new_bravia:
                        for k,v in new_bravia.items():
                            if v is not None:self._bravia_status[k]=v
                    else:
                        self.bravia._consecutive_failures+=1
                        if self.bravia._consecutive_failures>=self.bravia.MAX_CONSECUTIVE_FAILURES:
                            self.bravia._is_healthy=False
                except FuturesTimeoutError:
                    self.logger.debug("Bravia status timeout")
                    self.bravia._consecutive_failures+=1
                try:
                    new_kiri=kiri_future.result(timeout=self.API_TIMEOUT)
                    for k,v in new_kiri.items():
                        if v is not None:self._kirigamine_status[k]=v
                except FuturesTimeoutError:
                    self.logger.debug("Kirigamine status timeout")
                try:
                    new_kiri_bed=kiri_bed_future.result(timeout=self.API_TIMEOUT)
                    for k,v in new_kiri_bed.items():
                        if v is not None:self._kirigamine_bedroom_status[k]=v
                except FuturesTimeoutError:
                    self.logger.debug("Kirigamine bedroom status timeout")
                if sb_living_future:
                    try:
                        new_sb=sb_living_future.result(timeout=self.API_TIMEOUT+3)
                        for k,v in new_sb.items():
                            if v is not None:self._switchbot_living_status[k]=v
                    except FuturesTimeoutError:
                        self.logger.debug("Switchbot living status timeout")
                if sb_bedroom_future:
                    try:
                        new_sb=sb_bedroom_future.result(timeout=self.API_TIMEOUT+3)
                        for k,v in new_sb.items():
                            if v is not None:self._switchbot_bedroom_status[k]=v
                    except FuturesTimeoutError:
                        self.logger.debug("Switchbot bedroom status timeout")
                if self.switchbot_co2.is_configured():
                    try:
                        co2_status=self.switchbot_co2.get_status()
                        if co2_status.get('co2'):
                            self._switchbot_living_status['co2']=co2_status['co2']
                            self._check_co2_automation(co2_status['co2'])
                    except Exception:
                        pass
                if self._bravia_status.get('power',False):
                    if self._brightness_sync_enabled:
                        brightness=self._hue_status.get('brightness',0.0)
                        target_mode=self._brightness_to_mode(brightness)
                        if target_mode!=self._last_mode:
                            if self.bravia.set_power_saving_mode(target_mode):
                                self._last_mode=target_mode
                    if self._volume_auto_enabled:
                        current_app=self._bravia_status.get('app','')
                        self._check_app_volume(current_app)
                self._apply_focus_lighting()
                self.away_detector.check()
                self.sleep_detector.check()
                self._save_switchbot_cache()
                if self._status_callback:self._status_callback(self._hue_status,self._bravia_status,self._kirigamine_status,self._kirigamine_bedroom_status,self._switchbot_living_status,self._switchbot_bedroom_status)
            except Exception as e:
                self.logger.error(f"Sync error: {e}")
            time.sleep(self.POLL_INTERVAL)
    def start(self)->bool:
        if self._running:return True
        if not self.hue.connect():return False
        self.bravia.set_reconnect_callback(self._on_bravia_reconnect)
        self._executor=ThreadPoolExecutor(max_workers=6,thread_name_prefix="HomeAPI")
        self._running=True
        self._thread=threading.Thread(target=self._sync_loop,daemon=True,name="AmbientSync")
        self._thread.start()
        return True
    def _save_switchbot_cache(self):
        cache={}
        for name,ctrl in self.switchbot_controllers.items():
            if ctrl._status_cache.get('temperature') is not None or ctrl._status_cache.get('co2') is not None:
                cache[name]={k:v for k,v in ctrl._status_cache.items() if v is not None}
        if cache:SwitchbotController.save_all_cache(cache)
    def stop(self):
        self._running=False
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread=None
        if self._executor:
            self._executor.shutdown(wait=False)
            self._executor=None
    def set_brightness_sync_enabled(self,enabled:bool):self._brightness_sync_enabled=enabled
    def set_volume_auto_enabled(self,enabled:bool):self._volume_auto_enabled=enabled
    def is_brightness_sync_enabled(self)->bool:return self._brightness_sync_enabled
    def is_volume_auto_enabled(self)->bool:return self._volume_auto_enabled
    def set_enabled(self,enabled:bool):self._brightness_sync_enabled=enabled
    def is_enabled(self)->bool:return self._brightness_sync_enabled
    def is_running(self)->bool:return self._running
    def get_hue_status(self)->Dict:return self._hue_status
    def get_bravia_status(self)->Dict:return self._bravia_status
    def get_kirigamine_status(self)->Dict:return self._kirigamine_status
    def get_kirigamine_bedroom_status(self)->Dict:return self._kirigamine_bedroom_status
    def get_switchbot_living_status(self)->Dict:return self._switchbot_living_status
    def get_switchbot_bedroom_status(self)->Dict:return self._switchbot_bedroom_status
    def update_config(self,config:Dict):
        self.config=config
        was_running=self._running
        if was_running:self.stop()
        self.hue=HueController(config.get('hue_ip',''),self.logger)
        self.bravia=BraviaController(config.get('bravia_ip',''),config.get('bravia_psk',''),self.logger)
        self.kirigamine=KirigamineController(config.get('kirigamine_ip',''),logger=self.logger)
        self.kirigamine_bedroom=KirigamineController(config.get('kirigamine_bedroom_ip',''),logger=self.logger)
        self.bravia.set_reconnect_callback(self._on_bravia_reconnect)
        self._sleep_detection_room=config.get('sleep_detection_room','')
        self.sleep_detector=SleepDetector(self.hue,self.bravia,self.monitor,self._sleep_detection_room,config.get('sleep_detection_minutes',1.0),self.logger)
        self.sleep_detector.set_enabled(config.get('sleep_detection_enabled',False))
        if self._sleep_callback:self.sleep_detector.set_callback(self._sleep_callback)
        self._focus_keep_rooms=config.get('focus_keep_rooms',[])
        self._hide_zone_members=config.get('hide_zone_members',False)
        self._last_known_volume=None
        self._volume_override_until=0.0
        self._reconnect_grace_until=0.0
        if was_running:self.start()
if __name__=="__main__":
    print(f"=== Home Cybernetics v{__version__} ===")
    print(f"phue:{PHUE_AVAILABLE} requests:{REQUESTS_AVAILABLE}")
