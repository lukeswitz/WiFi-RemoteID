-------------------
# New route: USB port selection for multiple ports.
# ----------------------
@app.route('/select_ports', methods=['GET'])
def select_ports_get():
    ports = list(serial.tools.list_ports.comports())
    return render_template_string(PORT_SELECTION_PAGE, ports=ports, logo_ascii=LOGO_ASCII, bottom_ascii=BOTTOM_ASCII)


@app.route('/select_ports', methods=['POST'])
def select_ports_post():
    global SELECTED_PORTS
    # Get up to 3 ports; ignore empty values
    port1 = request.form.get('port1')
    port2 = request.form.get('port2')
    port3 = request.form.get('port3')
    if port1:
        SELECTED_PORTS['port1'] = port1
    if port2:
        SELECTED_PORTS['port2'] = port2
    if port3:
        SELECTED_PORTS['port3'] = port3
        if TAK_SETTINGS.get('enabled'):
    # Reload TLS context
    p12_path = os.path.join(BASE_DIR, 'tak.p12')
    pw_path = os.path.join(BASE_DIR, 'tak_password.txt')
    p12_password = None
    if os.path.isfile(pw_path):
        with open(pw_path, 'rb') as pf:
            p12_password = pf.read()
    global_tls_context = setup_tls_context(p12_path, p12_password, TAK_SETTINGS.get('skipVerify', True))
    init_tak_client()

    # Start serial-reader threads for selected ports
    for port in SELECTED_PORTS.values():
        serial_connected_status[port] = False
        start_serial_thread(port)
        # If TAK mode is enabled, initialize TAK client after threads start
        if TAK_SETTINGS.get('enabled', False):
            init_tak_client()

    # Redirect to main page
    return redirect(url_for('index'))

    # Background initialization: TAK client & serial-reader threads
    def init_mapping():
        global _tak_client, _cot_messenger
        # 1) TAK setup (only if enabled)
        if TAK_SETTINGS.get('enabled', False):
            host, port_str = TAK_SETTINGS['endpoint'].split(':')
            # Load stored certificate and password
            p12_path = os.path.join(BASE_DIR, 'tak.p12')
            with open(os.path.join(BASE_DIR, 'tak_password.txt'), 'r') as f:
                p12_password = f.read().strip()
            # Initialize TLS socket and connect
            tls_sock = init_tak_client(
                p12_path,
                p12_password,
                host,
                int(port_str),
                skip_verify=TAK_SETTINGS.get('skipVerify', True)
            )
            _tak_client = tls_sock
            _cot_messenger = CotMessenger(tak_client=_tak_client)
        else:
            _tak_client = None
            _cot_messenger = None

        # 2) Start serial-reader threads
        for port in SELECTED_PORTS.values():
            serial_connected_status[port] = False
            start_serial_thread(port)

    # Fire & forget initialization
    threading.Thread(target=init_mapping, daemon=True).start()

    # Return the redirect immediately
    return response


# ----------------------
# ASCII art blocks
# ----------------------
BOTTOM_ASCII = r"""
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⣄⣠⣀⡀⣀⣠⣤⣤⣤⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣄⢠⣠⣼⣿⣿⣿⣟⣿⣿⣿⣿⣿⣿⣿⡿⠋⠀⠀⠀⢠⣤⣦⡄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠰⢦⣄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⣼⣿⣟⣾⣿⣽⣿⣿⣅⠈⠉⠻⣿⣿⣿⣿⣿⡿⠇⠀⠀⠀⠀⠀⠉⠀⠀⠀⠀⠀⢀⡶⠒⢉⡀⢠⣤⣶⣶⣿⣷⣆⣀⡀⠀⢲⣖⠒⠀⠀⠀⠀⠀⠀⠀
⢀⣤⣾⣶⣦⣤⣤⣶⣿⣿⣿⣿⣿⣿⣽⡿⠻⣷⣀⠀⢻⣿⣿⣿⡿⠟⠀⠀⠀⠀⠀⠀⣤⣶⣶⣤⣀⣀⣬⣷⣦⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣶⣦⣤⣦⣼⣀⠀
⠈⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⠛⠓⣿⣿⠟⠁⠘⣿⡟⠁⠀⠘⠛⠁⠀⠀⢠⣾⣿⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⠏⠙⠁
⠀⠀⠸⠟⠋⠀⠈⠙⣿⣿⣿⣿⣿⣿⣷⣦⡄⣿⣿⣿⣆⠀⠀⠀⠀⠀⠀⠀⠀⣼⣆⢘⣿⣯⣼⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡉⠉⢱⡿⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣟⡿⠦⠀⠀⠀⠀⠀⠀⠀⠙⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⡗⠀⠈⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢻⣿⣿⣿⣿⣿⣿⣿⣿⠋⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⢿⣿⣉⣿⡿⢿⢷⣾⣾⣿⣞⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠋⣠⠟⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠹⣿⣿⣿⠿⠿⣿⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣾⣿⣿⣷⣦⣶⣦⣼⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣷⠈⠛⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⠻⣿⣤⡖⠛⠶⠤⡀⠀⠀⠀⠀⠀⠀⠀⢰⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⠁⠙⣿⣿⠿⢻⣿⣿⡿⠋⢩⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠙⠧⣤⣦⣤⣄⡀⠀⠀⠀⠀⠀⠘⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡇⠀⠀⠀⠘⣧⠀⠈⣹⡻⠇⢀⣿⡆⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⣿⣿⣿⣿⣿⣤⣀⡀⠀⠀⠀⠀⠀⠀⠀⠀⠈⢽⣿⣿⣿⣿⠋⠀⠀⠀⠀⠀⠀⠀⠀⠹⣷⣴⣿⣷⢲⣦⣤⡀⢀⡀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⢿⣿⣿⣿⣿⣿⣿⠟⠀⠀⠀⠀⠀⠀⠀⢸⣿⣿⣿⣿⣷⢀⡄⠀⠀⠀⠀⠀⠀⠀⠀⠈⠉⠂⠛⣆⣤⡜⣟⠋⠙⠂⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢹⣿⣿⣿⣿⠟⠀⠀⠀⠀⠀⠀⠀⠀⠘⣿⣿⣿⣿⠉⣿⠃⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣤⣾⣿⣿⣿⣿⣆⠀⠰⠄⠀⠉⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣸⣿⣿⡿⠃⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢹⣿⡿⠃⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢻⣿⠿⠿⣿⣿⣿⠇⠀⠀⢀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣿⡿⠛⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⢻⡇⠀⠀⢀⣼⠗⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⣿⠃⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⠁⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⠒⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
"""

LOGO_ASCII = r"""
        _____                .__      ________          __                 __       
       /     \   ____   _____|  |__   \______ \   _____/  |_  ____   _____/  |_     
      /  \ /  \_/ __ \ /  ___/  |  \   |    |  \_/ __ \   __\/ __ \_/ ___\   __\    
     /    Y    \  ___/ \___ \|   Y  \  |    `   \  ___/|  | \  ___/\  \___|  |      
     \____|__  /\___  >____  >___|  / /_______  /\___  >__|  \___  >\___  >__|      
             \/     \/     \/     \/          \/     \/     \/          \/     \/          
________                                  _____                                     
\______ \_______  ____   ____   ____     /     \ _____  ______ ______   ___________ 
 |    |  \_  __ \/  _ \ /    \_/ __ \   /  \ /  \\__  \ \____ \\____ \_/ __ \_  __ \
 |    `   \  | \(  <_> )   |  \  ___/  /    Y    \/ __ \|  |_> >  |_> >  ___/|  | \/
/_______  /__|   \____/|___|  /\___  > \____|__  (____  /   __/|   __/ \___  >__|   
        \/                  \/     \/          \/     \/|__|   |__|        \/       
"""

@app.route('/')
def index():
    if (len(SELECTED_PORTS) == 0):
        return redirect(url_for('select_ports_get'))
    return HTML_PAGE

@app.route('/api/detections', methods=['GET'])
def api_detections():
    return jsonify(tracked_pairs)

@app.route('/api/detections', methods=['POST'])
def post_detection():
    detection = request.get_json()
    update_detection(detection)
    return jsonify({"status": "ok"}), 200

@app.route('/api/detections_history', methods=['GET'])
def api_detections_history():
    features = []
    for det in detection_history:
        if det.get("drone_lat", 0) == 0 and det.get("drone_long", 0) == 0:
            continue
        features.append({
            "type": "Feature",
            "properties": {
                "mac": det.get("mac"),
                "rssi": det.get("rssi"),
                "time": datetime.fromtimestamp(det.get("last_update")).isoformat(),
                "details": det
            },
            "geometry": {
                "type": "Point",
