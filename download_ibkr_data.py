from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.common import BarData
import datetime
import time
import pandas as pd
import threading
import pytz
import os
import json
import traceback # Keep for general error reporting if needed, e.g., in CSV write

# --- Global Variables ---
task_data_collection = {}  # Key: datetime.date object (target_end_date), Value: task dict
req_id_to_task_map = {}    # Key: reqId, Value: {'task_key': datetime.date, 'part_name': 'bid'/'ask'/'main'}
IS_BID_ASK_MODE = False    # Will be set in main based on WHAT_TO_SHOW_CONFIG
app_instance = None        # To hold the IBapi instance for access in helper functions
FILENAME_FOR_OUTPUT = "" # Will be set in main

# --- Constants ---
RETRYABLE_ERROR_CODES = {1100, 1101, 1102, 1300, 2103, 2105, 2107, 2150}
REQUEST_SPECIFIC_RETRYABLE_ERROR_CODES = {
    162, 321, 322, 366,
}
INFORMATIONAL_API_CODES = {
    2100, 2104, 2106, 2108, 2158, 2174, 10314
}
CSV_WRITE_ERROR_CODE = 999


class IBapi(EWrapper, EClient):
    def __init__(self, contract_sec_type: str, expected_timezone_str: str = "America/New_York"):
        EClient.__init__(self, self)
        self.req_id_counter = 0
        self.contract_sec_type = contract_sec_type
        try:
            self.expected_timezone = pytz.timezone(expected_timezone_str)
            print(f"IBapi initialized. Timestamps from IBKR without explicit TZ will be interpreted as '{self.expected_timezone}'. Contract SecType: {self.contract_sec_type}")
        except pytz.exceptions.UnknownTimeZoneError:
            print(f"Warning: Unknown timezone '{expected_timezone_str}' in IBapi init. Defaulting to UTC.")
            self.expected_timezone = pytz.utc
        self.connection_lost = False
        self.is_connected_event = threading.Event()
        self.next_valid_req_id_ready = threading.Event()

    def nextValidId(self, orderId: int):
        super().nextValidId(orderId)
        self.req_id_counter = orderId
        print(f"NextValidId: {orderId}. Connection confirmed. Ready for requests.")
        self.is_connected_event.set()
        self.next_valid_req_id_ready.set()
        self.connection_lost = False

    def get_next_req_id(self):
        self.next_valid_req_id_ready.wait(timeout=10)
        if not self.next_valid_req_id_ready.is_set():
            print("Error: nextValidId not received. Cannot generate request ID.")
            return -1
        current_id = self.req_id_counter
        self.req_id_counter += 1
        return current_id

    def historicalData(self, reqId: int, bar: BarData):
        t_str_original = bar.date
        t_parts = bar.date.split(' ')
        processed_datetime_utc = None

        if len(t_parts) > 1 and ':' in t_parts[1]: # Has time component
            datetime_str_component = t_parts[0] + ' ' + t_parts[1]
            try:
                naive_datetime = datetime.datetime.strptime(datetime_str_component, '%Y%m%d %H:%M:%S')
                if len(t_parts) == 3: # Has explicit timezone from IBKR
                    ibkr_timezone_str = t_parts[2]
                    try:
                        ibkr_timezone = pytz.timezone(ibkr_timezone_str)
                        localized_datetime = ibkr_timezone.localize(naive_datetime)
                        processed_datetime_utc = localized_datetime.astimezone(pytz.utc)
                    except pytz.exceptions.UnknownTimeZoneError:
                        # Fallback to expected_timezone if IBKR timezone is unknown
                        print(f"Warning (ReqId {reqId}): Unknown timezone '{ibkr_timezone_str}' from IBKR. Using instrument's expected TZ '{self.expected_timezone}'.")
                        localized_datetime = self.expected_timezone.localize(naive_datetime)
                        processed_datetime_utc = localized_datetime.astimezone(pytz.utc)
                else: # No explicit timezone from IBKR, assume instrument's expected_timezone
                    localized_datetime = self.expected_timezone.localize(naive_datetime)
                    processed_datetime_utc = localized_datetime.astimezone(pytz.utc)
            except ValueError:
                print(f"Error (ReqId {reqId}): Parsing datetime '{t_str_original}'. Skipping.")
                return
        else: # Date only, no time component (e.g. daily bars - though script aims for 1 min)
            try:
                naive_datetime = datetime.datetime.strptime(t_parts[0], '%Y%m%d')
                # For daily bars, assume it represents the start of the day in the instrument's timezone
                localized_datetime = self.expected_timezone.localize(naive_datetime)
                processed_datetime_utc = localized_datetime.astimezone(pytz.utc)
            except ValueError:
                print(f"Error (ReqId {reqId}): Parsing date '{t_str_original}'. Skipping.")
                return

        if processed_datetime_utc is None: return

        if reqId in req_id_to_task_map:
            map_entry = req_id_to_task_map[reqId]
            task_key = map_entry['task_key']
            part_name = map_entry['part_name']

            if task_key in task_data_collection:
                task = task_data_collection[task_key]
                if part_name in task['parts']:
                    part = task['parts'][part_name]
                    
                    is_crypto = (self.contract_sec_type == "CRYPTO")
                    volume_val = 0
                    if bar.volume is not None and str(bar.volume).lower() != 'nan' and bar.volume != -1:
                        try:
                            volume_val = float(bar.volume) if is_crypto else int(float(bar.volume))
                        except ValueError:
                            volume_val = 0.0 if is_crypto else 0 # Default on conversion error
                    else: # None, NaN, or -1 volume
                        volume_val = 0.0 if is_crypto else 0

                    data_to_append = {
                        'date': processed_datetime_utc, 'open': bar.open, 'high': bar.high,
                        'low': bar.low, 'close': bar.close, 'volume': volume_val
                    }
                    part['data'].append(data_to_append)
                else: print(f"Warning (ReqId {reqId}): Part '{part_name}' not in task '{task_key}'. Bar ignored.")
            else: print(f"Warning (ReqId {reqId}): Task key '{task_key}' not in collection. Bar ignored.")
        else: print(f"Warning: Received historicalData for unmapped reqId: {reqId}. Bar ignored.")

    def historicalDataEnd(self, reqId: int, start_str_api: str, end_str_api: str):
        if reqId not in req_id_to_task_map:
            print(f"Warning: historicalDataEnd for unknown reqId: {reqId}.")
            return

        map_entry = req_id_to_task_map[reqId]
        task_key = map_entry['task_key']
        part_name = map_entry['part_name']

        if task_key not in task_data_collection:
            print(f"Warning: historicalDataEnd for reqId {reqId}, task key '{task_key}' not in collection.")
            return
        
        task = task_data_collection[task_key]
        target_end_date_display = task['end_date_obj_for_req'].strftime('%Y-%m-%d')

        if part_name not in task['parts']:
            print(f"Warning: historicalDataEnd for reqId {reqId}, part '{part_name}' not in task '{target_end_date_display}'.")
            return
        
        part = task['parts'][part_name]
        if not part['data']:
            print(f"No data received for {part_name.upper()} part (ReqId {reqId}, Task: {target_end_date_display}). Marking part as complete (empty).")
        part['status'] = "complete"
        print(f"{part_name.upper()} part (ReqId {reqId}) for task {target_end_date_display} processing complete.")

        # Update overall task status
        if IS_BID_ASK_MODE:
            if task['parts']['bid']['status'] == "complete" and task['parts']['ask']['status'] == "complete":
                if task['overall_status'] not in ["failed_permanent", "complete_final"]: 
                    task['overall_status'] = "ready_to_merge"
                    print(f"Task {target_end_date_display} (BID_ASK): Both BID and ASK parts complete. Ready to merge.")
        else: # Not BID_ASK mode
            if task['parts']['main']['status'] == "complete":
                if task['overall_status'] not in ["failed_permanent", "complete_final"]:
                    task['overall_status'] = "ready_to_write"
                    print(f"Task {target_end_date_display} (MAIN): Part complete. Ready to write.")
        
        if reqId in req_id_to_task_map:
            del req_id_to_task_map[reqId]


    def error(self, reqId: int, errorCode: int, errorString: str, advancedOrderRejectJson: str = ""):
        adv_reject_str = f" AdvancedOrderReject: {advancedOrderRejectJson}" if advancedOrderRejectJson else ""
        log_msg = f"Error. ReqId: {reqId}, Code: {errorCode}, Msg: {errorString}{adv_reject_str}"

        if errorCode in INFORMATIONAL_API_CODES:
            print(f"Informational/Warning (Code: {errorCode}, ReqId: {reqId}): {errorString}")
            return

        print(log_msg) # Log non-informational errors

        if errorCode in RETRYABLE_ERROR_CODES: # Typically connection-related
            if not self.connection_lost:
                print(f"Connection error detected (Code: {errorCode}). Setting connection_lost = True.")
                self.connection_lost = True
            return # Let main loop handle reconnection

        current_task = None
        part = None
        part_name_for_error = None
        task_key_for_error = None

        if reqId != -1 and reqId in req_id_to_task_map:
            map_entry = req_id_to_task_map[reqId]
            task_key_for_error = map_entry['task_key']
            part_name_for_error = map_entry['part_name']
            if task_key_for_error in task_data_collection:
                current_task = task_data_collection[task_key_for_error]
                if part_name_for_error in current_task['parts']:
                    part = current_task['parts'][part_name_for_error]
        
        # Handle "no data" as a form of completion for the part
        if errorCode == 10314 or \
           (errorCode == 162 and ("query returned no data" in errorString or ("HMDS query error" in errorString and "permissions" not in errorString.lower()))):
            if part and current_task: # Ensure part and task context exists
                print(f"Code {errorCode} (No data) for ReqId {reqId} ({part_name_for_error} part of task {task_key_for_error.strftime('%Y-%m-%d')}). Marking part as complete (empty).")
                part['status'] = "complete"
                part['last_error_code'] = errorCode
                # Trigger overall status check, similar to historicalDataEnd
                if IS_BID_ASK_MODE:
                    if current_task['parts']['bid']['status'] == "complete" and current_task['parts']['ask']['status'] == "complete":
                        if current_task['overall_status'] not in ["failed_permanent", "complete_final"]:
                             current_task['overall_status'] = "ready_to_merge"
                else:
                    if current_task['parts']['main']['status'] == "complete":
                         if current_task['overall_status'] not in ["failed_permanent", "complete_final"]:
                            current_task['overall_status'] = "ready_to_write"
                if reqId in req_id_to_task_map: del req_id_to_task_map[reqId] # Part is done
            else:
                print(f"Code {errorCode} (No data) received for ReqId {reqId}, but no associated task/part found in map. Error: {errorString}")
            return

        if part and current_task: # Request-specific error for an active part
            if part['status'] in ["complete", "failed_permanent"] or current_task['overall_status'] in ["complete_final", "failed_permanent"]:
                print(f"Error {errorCode} for already settled part/task. Ignoring.")
                return

            new_part_status = part['status']
            if errorCode in REQUEST_SPECIFIC_RETRYABLE_ERROR_CODES: # e.g. pacing violation, temporary issue
                if errorCode == 162 and "no market data permissions" in errorString.lower(): # 162 can be permanent if permissions
                    new_part_status = "failed_permanent"
                else:
                    new_part_status = "error_retry"
            elif errorCode == 200 or errorCode == 10299: # No security def / Invalid whatToShow / Bad contract
                new_part_status = "failed_permanent"
            else: # Other unhandled errors specific to this part are treated as permanent
                new_part_status = "failed_permanent" 
            
            part['status'] = new_part_status
            part['last_error_code'] = errorCode
            if new_part_status == "error_retry":
                 part.setdefault('retry_count', 0) # Ensure retry_count exists
            
            if new_part_status == "failed_permanent":
                current_task['overall_status'] = "failed_permanent"
                print(f"Overall task {task_key_for_error.strftime('%Y-%m-%d')} marked as failed_permanent due to {part_name_for_error} part failure (Code: {errorCode}).")
            
            # Clean up req_id if part is not going to be retried by this specific error handling
            if new_part_status != "error_retry" and reqId in req_id_to_task_map: 
                 del req_id_to_task_map[reqId]
            return
        
        # General errors not tied to a specific request (reqId == -1) or unmapped reqId
        if reqId == -1: print(f"General system message (Code: {errorCode}). Not connection related.")
        # else: (error for a reqId not in map, already logged by log_msg)


def load_config_from_path(config_path="datadownload_config.json"):
    try:
        with open(config_path, 'r') as f: config_data = json.load(f)
        print(f"Configuration loaded from {config_path}")
        return config_data
    except Exception as e:
        print(f"Error loading config '{config_path}': {e}. Exiting."); exit(1)

def request_historical_data_for_part(app_ref: IBapi, contract_obj: Contract, end_date_time_api_str: str, req_id_val: int, use_rth_param: int, what_to_show_for_part: str):
    print(f"Requesting data: ReqId={req_id_val}, Contract={contract_obj.symbol}, EndDateTime(UTC)='{end_date_time_api_str}', UseRTH={use_rth_param}, WhatToShow={what_to_show_for_part}")
    app_ref.reqHistoricalData(reqId=req_id_val, contract=contract_obj, endDateTime=end_date_time_api_str,
                              durationStr="1 W", barSizeSetting="1 min", whatToShow=what_to_show_for_part,
                              useRTH=use_rth_param, formatDate=1, keepUpToDate=False, chartOptions=[])

def process_and_write_task_data(task_key, task_config, filename_to_write, is_bid_ask):
    global task_data_collection
    task = task_data_collection[task_key]

    df_to_write = None
    if is_bid_ask:
        if task['parts']['bid']['status'] != "complete" or task['parts']['ask']['status'] != "complete":
            print(f"Task {task_key.strftime('%Y-%m-%d')}: BID/ASK parts not ready for merge. Skipping write.")
            return False 

        df_bid = pd.DataFrame(task['parts']['bid']['data'])
        df_ask = pd.DataFrame(task['parts']['ask']['data'])

        if df_bid.empty and df_ask.empty:
            print(f"Task {task_key.strftime('%Y-%m-%d')}: Both BID and ASK data are empty. Nothing to write.")
            task['overall_status'] = "complete_final"
            task['parts']['bid']['data'] = [] # Clear memory
            task['parts']['ask']['data'] = [] # Clear memory
            return True 

        if not df_bid.empty:
            df_bid.rename(columns={'open': 'bid_open', 'high': 'bid_high', 'low': 'bid_low', 'close': 'bid_close', 'volume': 'bid_volume'}, inplace=True)
        if not df_ask.empty:
            df_ask.rename(columns={'open': 'ask_open', 'high': 'ask_high', 'low': 'ask_low', 'close': 'ask_close', 'volume': 'ask_volume'}, inplace=True)

        if not df_bid.empty and not df_ask.empty:
            df_to_write = pd.merge(df_bid, df_ask, on='date', how='outer')
        elif not df_bid.empty:
            df_to_write = df_bid
            for col in ['ask_open', 'ask_high', 'ask_low', 'ask_close', 'ask_volume']:
                if col not in df_to_write.columns: df_to_write[col] = pd.NA # Use pandas NA for missing float/int data
        elif not df_ask.empty:
            df_to_write = df_ask
            for col in ['bid_open', 'bid_high', 'bid_low', 'bid_close', 'bid_volume']:
                if col not in df_to_write.columns: df_to_write[col] = pd.NA
        
        task['parts']['bid']['data'] = [] 
        task['parts']['ask']['data'] = []

    else: # Not BID_ASK (main part)
        if task['parts']['main']['status'] != "complete":
            print(f"Task {task_key.strftime('%Y-%m-%d')}: MAIN part not ready for write. Skipping.")
            return False
        
        df_to_write = pd.DataFrame(task['parts']['main']['data'])
        task['parts']['main']['data'] = [] 

    if df_to_write is None or df_to_write.empty:
        print(f"Task {task_key.strftime('%Y-%m-%d')}: No data to write after processing.")
        task['overall_status'] = "complete_final"
        return True

    if 'date' not in df_to_write.columns:
        print(f"Error Task {task_key.strftime('%Y-%m-%d')}: 'date' column missing. Cannot write.")
        task['overall_status'] = "failed_permanent" 
        return True 

    df_to_write['date'] = pd.to_datetime(df_to_write['date'], utc=True)
    df_to_write.sort_values('date', inplace=True)
    df_to_write.drop_duplicates(subset=['date'], keep='first', inplace=True)
    # Format date as string for CSV after all datetime operations
    df_to_write['date'] = df_to_write['date'].dt.strftime('%Y-%m-%d %H:%M:%S')

    file_exists = os.path.exists(filename_to_write)
    # Header should be written if file doesn't exist OR if it exists but is empty
    write_header = not file_exists or (file_exists and os.path.getsize(filename_to_write) == 0)
    try:
        df_to_write.to_csv(filename_to_write, mode='a', header=write_header, index=False)
        print(f"Data for task {task_key.strftime('%Y-%m-%d')} successfully written to {filename_to_write}")
        task['overall_status'] = "complete_final"
        return True
    except Exception as e:
        print(f"Error writing task {task_key.strftime('%Y-%m-%d')} to CSV '{filename_to_write}': {e}.")
        task['overall_status'] = "error_writing_csv" 
        # traceback.print_exc() # Optionally uncomment for more detail on CSV write errors
        return False

def main():
    global IS_BID_ASK_MODE, task_data_collection, req_id_to_task_map, app_instance, FILENAME_FOR_OUTPUT

    config = load_config_from_path()
    contract_cfg = config.get("contract_details", {})
    instrument_tz_str = config.get("instrument_timezone", "America/New_York")

    contract = Contract()
    contract.symbol = contract_cfg.get("symbol")
    contract.secType = contract_cfg.get("secType")
    contract.exchange = contract_cfg.get("exchange")
    contract.currency = contract_cfg.get("currency")
    contract.primaryExchange = contract_cfg.get("primary_exchange", "") # Optional, use if needed

    if not all([contract.symbol, contract.secType, contract.exchange, contract.currency]):
        print("Error: Essential contract details (symbol, secType, exchange, currency) missing in config. Exiting."); return
    print(f"Contract: {contract.symbol} ({contract.secType}) on {contract.exchange} in {contract.currency}")

    api_cfg = config.get("api_settings", {})
    IBKR_PORT = api_cfg.get("ibkr_port", 7497)
    CLIENT_ID = api_cfg.get("client_id", 1)
    MAX_ACTIVE_REQUESTS = api_cfg.get("max_active_requests", 5) 
    INTER_REQUEST_DELAY = api_cfg.get("inter_request_delay_seconds", 3) 
    MAX_CONNECT_RETRIES = api_cfg.get("max_connect_retries", 5)
    CONNECT_RETRY_DELAY = api_cfg.get("connect_retry_delay_seconds", 30)
    MAX_PART_RETRIES = api_cfg.get("max_request_retries", 3) 
    REQUEST_RETRY_DELAY = api_cfg.get("request_retry_delay_seconds", 20)
    USE_RTH_CONFIG = api_cfg.get("use_rth", 1) # 1 for RTH, 0 for all hours
    WHAT_TO_SHOW_CONFIG = api_cfg.get("what_to_show", "TRADES").upper()
    IS_BID_ASK_MODE = (WHAT_TO_SHOW_CONFIG == "BID_ASK")
    print(f"WhatToShow: {WHAT_TO_SHOW_CONFIG}, Is BID_ASK Mode: {IS_BID_ASK_MODE}, UseRTH: {USE_RTH_CONFIG}")

    date_range_cfg = config.get("date_range", {})
    today = datetime.date.today()
    # Default start date to beginning of current year if not specified
    default_start_year = today.year - 1 if today.month == 1 and today.day == 1 else today.year # Sensible default
    overall_start_date = datetime.date(
        date_range_cfg.get("start_year", default_start_year),
        date_range_cfg.get("start_month", 1),
        date_range_cfg.get("start_day", 1)
    )
    # Default end date to today if not specified
    overall_end_date = datetime.date(
        date_range_cfg.get("end_year", today.year),
        date_range_cfg.get("end_month", today.month),
        date_range_cfg.get("end_day", today.day)
    )
    print(f"Overall date range: {overall_start_date} to {overall_end_date}")

    output_cfg = config.get("output", {})
    filename_template = output_cfg.get("filename_template", "{symbol}_1min_{start_year}-{end_year}_utc.csv")
    symbol_for_file = (contract.symbol + contract.currency) if contract.secType == "CASH" else contract.symbol
    sanitized_symbol = symbol_for_file.replace("/", "_").replace(" ", "_") # Make symbol filename-safe
    
    # Use overall_start_date and overall_end_date for filename consistency across runs
    base_filename, ext = os.path.splitext(filename_template.format(
        symbol=sanitized_symbol, 
        start_year=overall_start_date.year, 
        end_year=overall_end_date.year 
    ))
    suffix = "_quote" if WHAT_TO_SHOW_CONFIG in ["BID_ASK", "BID", "ASK", "MIDPOINT"] else "_trade" 
    FILENAME_FOR_OUTPUT = f"{base_filename}{suffix}{ext}"
    print(f"Output file will be: {FILENAME_FOR_OUTPUT}")

    current_run_start_date = overall_start_date
    # Resume logic
    if os.path.exists(FILENAME_FOR_OUTPUT) and os.path.getsize(FILENAME_FOR_OUTPUT) > 0:
        try:
            df_existing = pd.read_csv(FILENAME_FOR_OUTPUT, usecols=['date'])
            if not df_existing.empty and 'date' in df_existing.columns:
                valid_dates = df_existing['date'].dropna()
                if not valid_dates.empty:
                    last_date_str = valid_dates.iloc[-1]
                    last_recorded_dt = pd.to_datetime(last_date_str, utc=True) # Assume dates in CSV are UTC
                    resume_date_candidate = (last_recorded_dt.date() + datetime.timedelta(days=1))
                    # Only update if resume_date_candidate is later than configured overall_start_date
                    if resume_date_candidate > current_run_start_date: 
                        current_run_start_date = resume_date_candidate
                        print(f"Resuming from: {current_run_start_date}")
                else:
                    print(f"Warning: Existing file '{FILENAME_FOR_OUTPUT}' has 'date' column but no valid date entries. Starting from configured start_date.")
            else:
                 print(f"Warning: Existing file '{FILENAME_FOR_OUTPUT}' is not empty but lacks a 'date' column or is unreadable as CSV. Starting from configured start_date.")
        except Exception as e:
            print(f"Warning: Error reading existing file '{FILENAME_FOR_OUTPUT}' for resume: {e}. Starting from configured start_date.")
            # traceback.print_exc() # Optionally uncomment for more detail on resume read errors

    if current_run_start_date > overall_end_date:
        print(f"All data from {overall_start_date} up to {overall_end_date} appears to be downloaded (resume date {current_run_start_date}). Exiting."); return

    # Initialize tasks based on the (potentially updated) current_run_start_date
    date_iter = overall_end_date 
    initial_tasks_dates = []
    while date_iter >= current_run_start_date:
        initial_tasks_dates.append(date_iter)
        date_iter -= datetime.timedelta(days=7) # Request 1 week at a time
    initial_tasks_dates.reverse() # Process older dates first

    for task_date_key in initial_tasks_dates:
        task_data_collection[task_date_key] = {
            'end_date_obj_for_req': task_date_key, # This is the *end date* for the 1W request
            'overall_status': "pending_initiation",
            'parts': {}
        }
        if IS_BID_ASK_MODE:
            task_data_collection[task_date_key]['parts']['bid'] = {'req_id': None, 'data': [], 'status': "pending_initiation", 'retry_count': 0, 'last_error_code': None}
            task_data_collection[task_date_key]['parts']['ask'] = {'req_id': None, 'data': [], 'status': "pending_initiation", 'retry_count': 0, 'last_error_code': None}
        else:
            task_data_collection[task_date_key]['parts']['main'] = {'req_id': None, 'data': [], 'status': "pending_initiation", 'retry_count': 0, 'last_error_code': None}
    
    if not task_data_collection:
        print(f"No tasks to process. Effective start date {current_run_start_date}, overall end date {overall_end_date}. Exiting."); return
    print(f"Initialized {len(task_data_collection)} tasks, to fetch data from effective start {current_run_start_date} up to {overall_end_date}.")

    app_instance = IBapi(contract_sec_type=contract.secType, expected_timezone_str=instrument_tz_str)
    api_thread = None

    # Connection Loop
    connected_successfully = False
    for attempt in range(MAX_CONNECT_RETRIES):
        print(f"Connection attempt {attempt + 1}/{MAX_CONNECT_RETRIES}...")
        if app_instance.isConnected(): app_instance.disconnect(); time.sleep(0.5) # Ensure clean disconnect
        if api_thread and api_thread.is_alive(): api_thread.join(timeout=5) # Wait for old thread
        
        app_instance.is_connected_event.clear()
        app_instance.next_valid_req_id_ready.clear()
        app_instance.connection_lost = False # Reset flag
        
        app_instance.connect('127.0.0.1', IBKR_PORT, clientId=CLIENT_ID)
        api_thread = threading.Thread(target=app_instance.run, daemon=True, name="IBAPIThread")
        api_thread.start()
        
        if app_instance.is_connected_event.wait(timeout=20) and app_instance.next_valid_req_id_ready.wait(timeout=15):
            print("Successfully connected."); connected_successfully = True; break
        else:
            print("Connection failed.")
            if app_instance.isConnected(): app_instance.disconnect() # Disconnect if partially connected
            if api_thread.is_alive(): api_thread.join(timeout=5)
            if attempt < MAX_CONNECT_RETRIES - 1: 
                print(f"Retrying connection in {CONNECT_RETRY_DELAY} seconds...")
                time.sleep(CONNECT_RETRY_DELAY)
            
    if not connected_successfully: 
        print("Max connection retries reached. Exiting."); return

    try:
        while True:
            if app_instance.connection_lost: 
                print("Connection lost. Attempting reconnect...")
                # Full reconnect logic similar to initial connection
                if app_instance.isConnected(): app_instance.disconnect(); time.sleep(0.5)
                if api_thread and api_thread.is_alive(): api_thread.join(timeout=5)
                
                app_instance.is_connected_event.clear()
                app_instance.next_valid_req_id_ready.clear()
                app_instance.connection_lost = False
                
                # Consider incrementing CLIENT_ID or using a different one if issues persist with reconnects
                app_instance.connect('127.0.0.1', IBKR_PORT, clientId=CLIENT_ID) 
                api_thread = threading.Thread(target=app_instance.run, daemon=True, name="IBAPIThread-Reconnect")
                api_thread.start()
                
                if app_instance.is_connected_event.wait(timeout=20) and app_instance.next_valid_req_id_ready.wait(timeout=15):
                    print("Reconnected successfully.")
                else:
                    print("Reconnect failed. Exiting to prevent further issues.")
                    if app_instance.isConnected(): app_instance.disconnect()
                    if api_thread.is_alive(): api_thread.join(timeout=5)
                    break # Exit main loop if reconnect fails

            # 1. Process completed tasks (Merge/Write)
            for task_key, task in list(task_data_collection.items()): # list() for safe modification
                if task['overall_status'] == "ready_to_merge" or task['overall_status'] == "ready_to_write":
                    # print(f"Processing task {task_key.strftime('%Y-%m-%d')} for writing/merging...") # Optional: less verbose
                    task['overall_status'] = "processing_csv" 
                    process_and_write_task_data(task_key, task, FILENAME_FOR_OUTPUT, IS_BID_ASK_MODE)

            active_req_count = len(req_id_to_task_map)

            # 2. Retry logic for parts with "error_retry" status
            if active_req_count < MAX_ACTIVE_REQUESTS: # Only attempt retries if slots are available
                for task_key, task in task_data_collection.items():
                    if task['overall_status'] in ["complete_final", "failed_permanent", "processing_csv"]: continue
                    if app_instance.connection_lost: break # Check connection before processing parts of a task
                    
                    for part_name, part_details in task['parts'].items():
                        if part_details['status'] == "error_retry" and active_req_count < MAX_ACTIVE_REQUESTS:
                            if part_details['retry_count'] < MAX_PART_RETRIES:
                                part_details['retry_count'] += 1
                                print(f"Retrying {part_name} for task {task_key.strftime('%Y-%m-%d')} (Attempt {part_details['retry_count']}/{MAX_PART_RETRIES}) after {REQUEST_RETRY_DELAY}s delay.")
                                time.sleep(REQUEST_RETRY_DELAY) # Delay before retry

                                new_req_id = app_instance.get_next_req_id()
                                if new_req_id == -1: app_instance.connection_lost = True; break 
                                
                                part_details['req_id'] = new_req_id
                                part_details['status'] = "incomplete" # Reset status
                                part_details['data'] = [] # Clear previous (potentially partial/error) data
                                req_id_to_task_map[new_req_id] = {'task_key': task_key, 'part_name': part_name}
                                
                                end_date_obj = task['end_date_obj_for_req']
                                # API expects endDateTime in instrument's local timezone (or UTC if specified)
                                naive_dt_local = datetime.datetime.combine(end_date_obj, datetime.time(23, 59, 59))
                                localized_dt_for_api = app_instance.expected_timezone.localize(naive_dt_local)
                                # Convert to UTC for the "YYYYMMDD HH:MM:SS UCT" format if instrument_timezone is not UTC
                                # If instrument_timezone IS UTC, this astimezone does nothing.
                                dt_utc_for_api_str = localized_dt_for_api.astimezone(pytz.utc).strftime("%Y%m%d %H:%M:%S UTC")
                                
                                what_to_show_for_this_part = part_name.upper() if IS_BID_ASK_MODE else WHAT_TO_SHOW_CONFIG
                                request_historical_data_for_part(app_instance, contract, dt_utc_for_api_str, new_req_id, USE_RTH_CONFIG, what_to_show_for_this_part)
                                active_req_count += 1
                                time.sleep(INTER_REQUEST_DELAY) # Pace subsequent new requests
                            else:
                                print(f"Max retries for {part_name} of task {task_key.strftime('%Y-%m-%d')} reached. Marking part and task failed.")
                                part_details['status'] = "failed_permanent"
                                task['overall_status'] = "failed_permanent" # Cascade failure to task
                                if part_details['req_id'] and part_details['req_id'] in req_id_to_task_map:
                                    del req_id_to_task_map[part_details['req_id']] # Clean up old req_id if any
                        if active_req_count >= MAX_ACTIVE_REQUESTS or app_instance.connection_lost: break
                    if active_req_count >= MAX_ACTIVE_REQUESTS or app_instance.connection_lost: break
            if app_instance.connection_lost: continue # Re-check connection at start of main while loop

            # 3. Initiate new requests for "pending_initiation" parts
            if active_req_count < MAX_ACTIVE_REQUESTS: # Only initiate if slots are available
                sorted_task_keys = sorted(task_data_collection.keys()) # Process older tasks first
                for task_key in sorted_task_keys:
                    task = task_data_collection[task_key]
                    if task['overall_status'] in ["complete_final", "failed_permanent", "processing_csv", "ready_to_merge", "ready_to_write"]:
                        continue
                    if app_instance.connection_lost: break

                    for part_name, part_details in task['parts'].items():
                        if part_details['status'] == "pending_initiation" and active_req_count < MAX_ACTIVE_REQUESTS:
                            # print(f"Initiating {part_name} for task {task_key.strftime('%Y-%m-%d')}") # Optional: less verbose
                            new_req_id = app_instance.get_next_req_id()
                            if new_req_id == -1: app_instance.connection_lost = True; break 
                            
                            part_details['req_id'] = new_req_id
                            part_details['status'] = "incomplete" # Mark as request sent
                            req_id_to_task_map[new_req_id] = {'task_key': task_key, 'part_name': part_name}

                            end_date_obj = task['end_date_obj_for_req']
                            naive_dt_local = datetime.datetime.combine(end_date_obj, datetime.time(23, 59, 59))
                            localized_dt_for_api = app_instance.expected_timezone.localize(naive_dt_local)
                            dt_utc_for_api_str = localized_dt_for_api.astimezone(pytz.utc).strftime("%Y%m%d %H:%M:%S UTC")
                            
                            what_to_show_for_this_part = part_name.upper() if IS_BID_ASK_MODE else WHAT_TO_SHOW_CONFIG
                            request_historical_data_for_part(app_instance, contract, dt_utc_for_api_str, new_req_id, USE_RTH_CONFIG, what_to_show_for_this_part)
                            active_req_count += 1
                            time.sleep(INTER_REQUEST_DELAY) 
                        if active_req_count >= MAX_ACTIVE_REQUESTS or app_instance.connection_lost: break
                    if active_req_count >= MAX_ACTIVE_REQUESTS or app_instance.connection_lost: break
            if app_instance.connection_lost: continue

            # 4. Check for overall completion
            all_tasks_done = True
            if not task_data_collection: # Should ideally not happen if initialized
                print("Warning: task_data_collection is empty, assuming completion but this is unexpected.")
            else:
                for task_key, task in task_data_collection.items():
                    if task['overall_status'] not in ["complete_final", "failed_permanent"]:
                        all_tasks_done = False
                        break
            
            if all_tasks_done:
                print("All tasks processed or no tasks to process.")
                break
            
            time.sleep(1) # Main loop delay

    except KeyboardInterrupt:
        print("KeyboardInterrupt. Shutting down...")
    except Exception as e_main: # Catch any other unexpected errors in main loop
        print(f"Unexpected error in main loop: {e_main}")
        traceback.print_exc()
    finally:
        # Summary of task statuses
        if task_data_collection:
            completed_final = sum(1 for t in task_data_collection.values() if t['overall_status'] == "complete_final")
            failed_permanent = sum(1 for t in task_data_collection.values() if t['overall_status'] == "failed_permanent")
            error_writing = sum(1 for t in task_data_collection.values() if t['overall_status'] == "error_writing_csv")
            other_statuses = len(task_data_collection) - completed_final - failed_permanent - error_writing
            
            print(f"\n--- Processing Summary ---")
            print(f"Total Tasks Initiated: {len(task_data_collection)}")
            print(f"  Completed & Written: {completed_final}")
            print(f"  Permanently Failed (data request/contract): {failed_permanent}")
            print(f"  Failed (CSV write error): {error_writing}")
            print(f"  Other Statuses (e.g. pending, error_retry, incomplete): {other_statuses}")

            if other_statuses > 0 or failed_permanent > 0 or error_writing > 0:
                 print("\n  Details for non-completed/failed tasks:")
                 for tk, tv in sorted(task_data_collection.items()): # Sort for consistent output
                     if tv['overall_status'] not in ["complete_final"]:
                         print(f"    Task for week ending {tk.strftime('%Y-%m-%d')}: Overall Status: {tv['overall_status']}")
                         for pn, pd_part_details in tv['parts'].items():
                             retry_info = f", Retries: {pd_part_details.get('retry_count',0)}/{MAX_PART_RETRIES}" if 'retry_count' in pd_part_details else ""
                             error_info = f", LastError: {pd_part_details.get('last_error_code','N/A')}" if pd_part_details.get('last_error_code') else ""
                             print(f"      Part '{pn}': Status: {pd_part_details['status']}{error_info}{retry_info}")
            print(f"--- End Summary ---")

        if app_instance and app_instance.isConnected(): 
            print("Disconnecting from IBKR...")
            app_instance.disconnect()
        if api_thread and api_thread.is_alive(): 
            print("Waiting for API thread to shut down...")
            api_thread.join(timeout=10) # Increased timeout for join
            if api_thread.is_alive():
                print("Warning: API thread did not shut down cleanly.")
        
        print(f"Script finished. Check data in {FILENAME_FOR_OUTPUT}")

if __name__ == "__main__":
    main()
