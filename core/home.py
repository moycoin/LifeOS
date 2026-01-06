#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json,re,subprocess,time,threading,ctypes,sys,logging
from concurrent.futures import ThreadPoolExecutor,TimeoutError as FuturesTimeoutError
from typing import Dict,Optional,Callable,List
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
ALLOWED_ROOMS=["リビングルーム","キッチン","廊下","化粧室","トイレ","浴室","寝室","シューズイン"]
class MonitorController:
    SC_MONITORPOWER=0xF170
    MONITOR_OFF=2
    MONITOR_ON=-1
    HWND_BROADCAST=0xFFFF
    WM_SYSCOMMAND=0x0112
    MOUSEEVENTF_MOVE=0x0001
    def __init__(self,logger:Optional[logging.Logger]=None):
        self._is_windows=sys.platform=='win32'
        self._monitors_off=False
        self.logger=logger or logging.getLogger(__name__)
    def _send_message(self,wparam:int)->bool:
        if not self._is_windows:return False
        try:
            ctypes.windll.user32.SendMessageW(self.HWND_BROADCAST,self.WM_SYSCOMMAND,self.SC_MONITORPOWER,wparam)
            return True
        except Exception as e:
            self.logger.error(f"Monitor control failed: {e}")
            return False
    def _simulate_mouse_move(self):
        if not self._is_windows:return
        try:
            ctypes.windll.user32.mouse_event(self.MOUSEEVENTF_MOVE,1,1,0,0)
            time.sleep(0.05)
            ctypes.windll.user32.mouse_event(self.MOUSEEVENTF_MOVE,-1,-1,0,0)
        except:pass
    def _send_key_event(self):
        if not self._is_windows:return
        try:
            VK_SHIFT=0x10
            KEYEVENTF_KEYUP=0x0002
            ctypes.windll.user32.keybd_event(VK_SHIFT,0,0,0)
            time.sleep(0.01)
            ctypes.windll.user32.keybd_event(VK_SHIFT,0,KEYEVENTF_KEYUP,0)
        except:pass
    def turn_off(self)->bool:
        if self._send_message(self.MONITOR_OFF):
            self._monitors_off=True
            self.logger.info("Monitor turned OFF")
            return True
        return False
    def turn_on(self)->bool:
        if not self._is_windows:return False
        success=False
        try:
            for _ in range(3):
                self._send_message(self.MONITOR_ON)
                time.sleep(0.1)
            self._simulate_mouse_move()
            time.sleep(0.1)
            self._send_key_event()
            time.sleep(0.2)
            for _ in range(2):
                self._send_message(self.MONITOR_ON)
                time.sleep(0.1)
            self._simulate_mouse_move()
            self._monitors_off=False
            success=True
            self.logger.info("Monitor wake sequence complete (5 msgs + mouse + key)")
        except Exception as e:
            self.logger.error(f"Monitor turn_on failed: {e}")
        return success
    def is_off(self)->bool:return self._monitors_off
class BraviaController:
    PACKAGE_MAP={'spotify':'Spotify','netflix':'Netflix','youtube':'YouTube','amazon':'Prime Video','disney':'Disney+','hulu':'Hulu','abema':'ABEMA','tver':'TVer','nhkplus':'NHK+','dazn':'DAZN','twitch':'Twitch','plex':'Plex','kodi':'Kodi','vlc':'VLC','crunchyroll':'Crunchyroll','funimation':'Funimation','tv.sony':'Live TV','settings':'Settings','launcher':'Home'}
    IRCC_VOLUME_UP='AAAAAQAAAAEAAAASAw=='
    IRCC_VOLUME_DOWN='AAAAAQAAAAEAAAATAw=='
    VOL_BURST_INTERVAL=0.05
    VOL_SETTLE_DELAY=0.15
    def __init__(self,ip:str,psk:str,logger:Optional[logging.Logger]=None):
        self.ip,self.psk,self.base_url=ip,psk,f"http://{ip}/sony"
        self._power_state,self._volume,self._app_name,self._power_saving,self._adb_connected=None,None,None,None,False
        self.logger=logger or logging.getLogger(__name__)
    def _request(self,service:str,method:str,params:list=None,version:str="1.0")->Optional[Dict]:
        if not REQUESTS_AVAILABLE:return None
        try:
            r=requests.post(f"{self.base_url}/{service}",json={"method":method,"params":params or[],"id":1,"version":version},headers={"X-Auth-PSK":self.psk,"Content-Type":"application/json"},timeout=3)
            return r.json() if r.status_code==200 else None
        except requests.exceptions.Timeout:
            self.logger.debug(f"Bravia request timeout: {method}")
            return None
        except requests.exceptions.RequestException as e:
            self.logger.debug(f"Bravia request error: {e}")
            return None
    def _send_ircc(self,code:str)->bool:
        if not REQUESTS_AVAILABLE:return False
        try:
            xml=f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body><u:X_SendIRCC xmlns:u="urn:schemas-sony-com:service:IRCC:1"><IRCCCode>{code}</IRCCCode></u:X_SendIRCC></s:Body></s:Envelope>'
            r=requests.post(f"http://{self.ip}/sony/IRCC",data=xml,headers={"X-Auth-PSK":self.psk,"Content-Type":"text/xml; charset=UTF-8","SOAPACTION":'"urn:schemas-sony-com:service:IRCC:1#X_SendIRCC"'},timeout=0.5)
            return r.status_code==200
        except:
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
        result=self._request("system","setPowerStatus",[{"status":on}])
        if result and "result" in result:
            self._power_state=on
            self.logger.info(f"Bravia Power {'ON' if on else 'OFF'}")
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
        current=self.get_volume()
        if current==target:return True
        diff=target-current
        ircc_code=self.IRCC_VOLUME_UP if diff>0 else self.IRCC_VOLUME_DOWN
        steps=abs(diff)
        for _ in range(min(steps,max_steps)):
            self._send_ircc(ircc_code)
            time.sleep(self.VOL_BURST_INTERVAL)
        time.sleep(self.VOL_SETTLE_DELAY)
        actual=self.get_volume()
        if actual==target:
            self._volume=actual
            return True
        overshoot=actual-target
        if overshoot!=0:
            fix_code=self.IRCC_VOLUME_DOWN if overshoot>0 else self.IRCC_VOLUME_UP
            for _ in range(abs(overshoot)):
                self._send_ircc(fix_code)
                time.sleep(self.VOL_BURST_INTERVAL)
            time.sleep(self.VOL_SETTLE_DELAY)
            actual=self.get_volume()
        self._volume=actual
        return actual==target
    def get_status(self)->Dict:
        return {'power':self.get_power_status(),'volume':self.get_volume(),'app':self.get_playing_content(),'power_saving':self.get_power_saving_mode()}
class HueController:
    def __init__(self,ip:str,room_name:str="リビングルーム",logger:Optional[logging.Logger]=None):
        self.ip,self.room_name=ip,room_name
        self._bridge:Optional[Bridge]=None
        self._connected=False
        self._group_cache:Dict[str,int]={}
        self.logger=logger or logging.getLogger(__name__)
    def connect(self)->bool:
        if not PHUE_AVAILABLE:return False
        try:
            self._bridge=Bridge(self.ip)
            self._bridge.connect()
            self._connected=True
            self._build_group_cache()
            self.logger.info(f"Hue connected to {self.ip}")
            return True
        except Exception as e:
            self.logger.error(f"Hue connection failed: {e}")
            self._connected=False
            return False
    def _build_group_cache(self):
        if not self._connected or not self._bridge:return
        try:
            self._group_cache.clear()
            for gid,info in self._bridge.get_group().items():
                name=info.get('name','')
                if name:
                    self._group_cache[name]=int(gid) if isinstance(gid,str) and gid.isdigit() else gid
        except Exception as e:
            self.logger.debug(f"Hue group cache build error: {e}")
    def _safe_set_group(self,group_name:str,param:str,value)->bool:
        if not self._connected or not self._bridge:return False
        try:
            gid=self._group_cache.get(group_name)
            if gid is None:
                self._build_group_cache()
                gid=self._group_cache.get(group_name)
            if gid is None:
                self.logger.debug(f"Group not found: {group_name}")
                return False
            self._bridge.set_group(gid,param,value)
            return True
        except Exception as e:
            self.logger.debug(f"Hue set_group error ({group_name}): {e}")
            return False
    def get_room_brightness(self)->float:
        if not self._connected or not self._bridge:return 0.0
        try:
            for gid,info in self._bridge.get_group().items():
                if info.get('name')==self.room_name:
                    action=info.get('action',{})
                    return (action.get('bri',0)/254) if action.get('on') else 0.0
            return 0.0
        except Exception as e:
            self.logger.debug(f"Hue brightness error: {e}")
            return 0.0
    def is_all_lights_off(self,room_name:str=None)->bool:
        if not self._connected or not self._bridge:return False
        try:
            target_room=room_name or self.room_name
            for gid,info in self._bridge.get_group().items():
                if info.get('name')==target_room:
                    return not info.get('state',{}).get('any_on',True)
            return False
        except Exception as e:
            self.logger.debug(f"Hue lights check error: {e}")
            return False
    def get_all_rooms(self)->Dict:
        if not self._connected or not self._bridge:return {}
        try:
            rooms={}
            for gid,info in self._bridge.get_group().items():
                name=info.get('name','')
                if name in ALLOWED_ROOMS:
                    action=info.get('action',{})
                    state=info.get('state',{})
                    is_on=state.get('any_on',False)
                    bri=(action.get('bri',0)/254) if is_on else 0.0
                    rooms[name]={'on':is_on,'bri':bri,'group_id':gid}
            return rooms
        except Exception as e:
            self.logger.debug(f"Hue get_all_rooms error: {e}")
            return {}
    def turn_off_all_except_living(self)->int:
        if not self._connected or not self._bridge:return 0
        count=0
        try:
            for gid,info in self._bridge.get_group().items():
                name=info.get('name','')
                if name in ALLOWED_ROOMS and name!=self.room_name:
                    if info.get('state',{}).get('any_on',False):
                        if self._safe_set_group(name,'on',False):
                            count+=1
        except Exception as e:
            self.logger.debug(f"Hue turn off error: {e}")
        return count
    def get_status(self)->Dict:
        return {'connected':self._connected,'room':self.room_name,'brightness':self.get_room_brightness(),'all_off':self.is_all_lights_off(),'all_rooms':self.get_all_rooms()}
class SleepDetector:
    def __init__(self,hue:HueController,bravia:BraviaController,monitor:MonitorController,target_room:str="リビングルーム",delay_minutes:float=1.0,logger:Optional[logging.Logger]=None):
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
                self.logger.info(f"Lights off detected in {self.target_room}")
            elif not self._is_sleeping:
                elapsed_min=(now-self._lights_off_since)/60.0
                if elapsed_min>=self.delay_minutes:
                    self._trigger_sleep()
                    return True
        else:
            if self._lights_off_since is not None:
                self.logger.info("Lights on - reset timer")
            self._lights_off_since=None
        return False
    def _trigger_sleep(self):
        if self._is_sleeping:return
        self._is_sleeping=True
        self.logger.info("Sleep detected! Turning off BRAVIA and monitors...")
        self.bravia.power_off()
        self.monitor.turn_off()
        if self._callback:self._callback(True)
    def wake(self)->bool:
        if not self._is_sleeping:return False
        self._is_sleeping=False
        self._lights_off_since=None
        self.logger.info("Waking up - monitors ON (enhanced)")
        result=self.monitor.turn_on()
        if self._callback:self._callback(False)
        return result
class AmbientSync:
    POLL_INTERVAL=3.0
    ACTIVITY_TIMEOUT=5.0
    API_TIMEOUT=2.0
    DEFAULT_THRESHOLDS={'off':60,'low':20,'high':1}
    DEFAULT_VOLUME_PROFILES={'Spotify':{'enabled':False,'volume':15},'Netflix':{'enabled':False,'volume':20},'YouTube':{'enabled':False,'volume':20},'Prime Video':{'enabled':False,'volume':20}}
    def __init__(self,config:Dict,logger:Optional[logging.Logger]=None):
        self.config=config
        self.logger=logger or logging.getLogger(__name__)
        self.hue=HueController(config.get('hue_ip',''),config.get('hue_room','リビングルーム'),self.logger)
        self.bravia=BraviaController(config.get('bravia_ip',''),config.get('bravia_psk',''),self.logger)
        self.monitor=MonitorController(self.logger)
        self.sleep_detector=SleepDetector(self.hue,self.bravia,self.monitor,config.get('hue_room','リビングルーム'),config.get('sleep_detection_minutes',1.0),self.logger)
        self._enabled=False
        self._running=False
        self._thread:Optional[threading.Thread]=None
        self._executor:Optional[ThreadPoolExecutor]=None
        self._last_mode:Optional[PowerSavingMode]=None
        self._last_app:Optional[str]=None
        self._status_callback:Optional[Callable]=None
        self._sleep_callback:Optional[Callable]=None
        self._hue_status:Dict={}
        self._bravia_status:Dict={}
        self._thresholds=config.get('thresholds',self.DEFAULT_THRESHOLDS.copy())
        self._volume_profiles=config.get('volume_profiles',self.DEFAULT_VOLUME_PROFILES.copy())
        self._last_input_time:float=0.0
        self._focus_lighting_enabled=config.get('focus_lighting',False)
        self._sleep_detection_enabled=config.get('sleep_detection_enabled',False)
        self.sleep_detector.set_enabled(self._sleep_detection_enabled)
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
    def get_volume_profiles(self)->Dict:return self._volume_profiles
    def update_user_activity(self,is_active:bool):
        if is_active:self._last_input_time=time.time()
    def set_focus_lighting(self,enabled:bool):
        self._focus_lighting_enabled=enabled
        self.config['focus_lighting']=enabled
        self.logger.info(f"Focus Lighting: {'ENABLED' if enabled else 'DISABLED'}")
    def set_sleep_detection(self,enabled:bool,delay_minutes:float=None):
        self._sleep_detection_enabled=enabled
        self.sleep_detector.set_enabled(enabled)
        if delay_minutes is not None:
            self.sleep_detector.set_delay(delay_minutes)
            self.config['sleep_detection_minutes']=delay_minutes
        self.config['sleep_detection_enabled']=enabled
        self.logger.info(f"Sleep Detection: {'ENABLED' if enabled else 'DISABLED'} (delay={self.sleep_detector.delay_minutes}min)")
    def wake_monitors(self)->bool:return self.sleep_detector.wake()
    def is_sleeping(self)->bool:return self.sleep_detector.is_sleeping()
    def _is_user_active(self)->bool:
        return (time.time()-self._last_input_time)<self.ACTIVITY_TIMEOUT
    def _apply_focus_lighting(self):
        if not self._focus_lighting_enabled:return
        if not self._is_user_active():return
        count=self.hue.turn_off_all_except_living()
        if count>0:self.logger.info(f"Focus: {count} rooms turned off")
    def _brightness_to_mode(self,brightness:float)->PowerSavingMode:
        pct=brightness*100
        if pct>self._thresholds.get('off',60):return PowerSavingMode.OFF
        elif pct>self._thresholds.get('low',20):return PowerSavingMode.LOW
        else:return PowerSavingMode.HIGH
    def _check_app_volume(self,current_app:str):
        if current_app==self._last_app:return
        if current_app and current_app!="Unknown":
            profile=self._volume_profiles.get(current_app)
            if profile and profile.get('enabled',False):
                target_vol=profile.get('volume',20)
                if self.bravia.set_volume(target_vol):
                    self.logger.info(f"Volume: {current_app} → {target_vol}")
        self._last_app=current_app
    def _fetch_hue_status(self)->Dict:
        try:
            return self.hue.get_status()
        except Exception as e:
            self.logger.debug(f"Hue status error: {e}")
            return {}
    def _fetch_bravia_status(self)->Dict:
        try:
            return self.bravia.get_status()
        except Exception as e:
            self.logger.debug(f"Bravia status error: {e}")
            return {}
    def _sync_loop(self):
        self.logger.info("Sync loop started")
        while self._running:
            try:
                hue_future=self._executor.submit(self._fetch_hue_status)
                bravia_future=self._executor.submit(self._fetch_bravia_status)
                try:
                    self._hue_status=hue_future.result(timeout=self.API_TIMEOUT)
                except FuturesTimeoutError:
                    self.logger.debug("Hue status timeout")
                    self._hue_status={}
                try:
                    self._bravia_status=bravia_future.result(timeout=self.API_TIMEOUT)
                except FuturesTimeoutError:
                    self.logger.debug("Bravia status timeout")
                    self._bravia_status={}
                if self._enabled and self._bravia_status.get('power',False):
                    brightness=self._hue_status.get('brightness',0.0)
                    target_mode=self._brightness_to_mode(brightness)
                    if target_mode!=self._last_mode:
                        if self.bravia.set_power_saving_mode(target_mode):
                            self._last_mode=target_mode
                    current_app=self._bravia_status.get('app','')
                    self._check_app_volume(current_app)
                self._apply_focus_lighting()
                self.sleep_detector.check()
                if self._status_callback:self._status_callback(self._hue_status,self._bravia_status)
            except Exception as e:
                self.logger.error(f"Sync error: {e}")
            time.sleep(self.POLL_INTERVAL)
    def start(self)->bool:
        if self._running:return True
        if not self.hue.connect():return False
        self._executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix="HomeAPI")
        self._running=True
        self._thread=threading.Thread(target=self._sync_loop,daemon=True,name="AmbientSync")
        self._thread.start()
        self.logger.info("AmbientSync started")
        return True
    def stop(self):
        self._running=False
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread=None
        if self._executor:
            self._executor.shutdown(wait=False)
            self._executor=None
        self.logger.info("AmbientSync stopped")
    def set_enabled(self,enabled:bool):self._enabled=enabled
    def is_enabled(self)->bool:return self._enabled
    def is_running(self)->bool:return self._running
    def get_hue_status(self)->Dict:return self._hue_status
    def get_bravia_status(self)->Dict:return self._bravia_status
    def update_config(self,config:Dict):
        self.config=config
        was_running=self._running
        if was_running:self.stop()
        self.hue=HueController(config.get('hue_ip',''),config.get('hue_room','リビングルーム'),self.logger)
        self.bravia=BraviaController(config.get('bravia_ip',''),config.get('bravia_psk',''),self.logger)
        self.sleep_detector=SleepDetector(self.hue,self.bravia,self.monitor,config.get('hue_room','リビングルーム'),config.get('sleep_detection_minutes',1.0),self.logger)
        self.sleep_detector.set_enabled(config.get('sleep_detection_enabled',False))
        if self._sleep_callback:self.sleep_detector.set_callback(self._sleep_callback)
        if was_running:self.start()
if __name__=="__main__":
    print(f"=== Home Cybernetics v{__version__} ===")
    print(f"phue:{PHUE_AVAILABLE} requests:{REQUESTS_AVAILABLE}")
