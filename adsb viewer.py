import math, time, threading, urllib.request, io, os, json
import pygame, sys
from concurrent.futures import ThreadPoolExecutor
from FlightRadar24 import FlightRadar24API

# --- Safe Defaults ---
DEFAULT_CONFIG = {
    "MY_LAT": 41.0082,        # Generic Istanbul Center
    "MY_LON": 28.9784,
    "RADIUS_KM": 30,
    "RADAR_NAME": "RADAR // ACTIVE"
}
BG = (10, 12, 16)
MISSING_TILE = (20, 24, 30)
GRID = (100, 50, 150)     #i know that the code is messy but it works
GRID_DIM = (40, 20, 65) 
SWEEP_COLOR = (140, 70, 210)  
HOME_COLOR = (0, 255, 200)
TEXT_COLOR = (240, 240, 245)      # Main text readout color
TEXT_DIM = (160, 160, 170)        # Muted text flavor 1
DIM_TEXT = (160, 160, 170)        # Muted text flavor 2 (Fixes line 375!)
PLANE_COLOR = (0, 230, 110)      # Neon green for active aircraft targets
SELECTED_COLOR = (255, 255, 0)   # Bright warning yellow for a locked/selected target
LABEL_COLOR = (130, 140, 160)   # Muted slate gray/blue for alternating text rows
# --- Load Config File Safely ---
config = DEFAULT_CONFIG.copy()
if os.path.exists("config.json"):
    try:
        with open("config.json", "r") as f:
            config.update(json.load(f))
    except Exception as e:
        print(f"Error loading config.json, using defaults. Error: {e}")

MY_LAT      = config["MY_LAT"]
MY_LON      = config["MY_LON"]
RADIUS_KM   = config["RADIUS_KM"]
RADAR_NAME  = config["RADAR_NAME"]
REFRESH_SEC = 10

# --- Display ---
WIDTH, HEIGHT = 900, 900
CX, CY = WIDTH // 2, HEIGHT // 2
RADAR_R = 380


# ── Tile system ──────────────────────────────────────────────
TILE_SIZE   = 256
TILE_CACHE  = {}          # (tx,ty,tz) -> pygame.Surface | "loading" | "failed"
TILE_LOCK   = threading.Lock()
FETCH_POOL  = ThreadPoolExecutor(max_workers=8)
os.makedirs("tile_cache", exist_ok=True)

TILE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"

def deg2tile(lat, lon, zoom):
    n = 2 ** zoom
    x = (lon + 180) / 360 * n
    y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
    return x, y

def _do_fetch(tx, ty, tz):
    """Download tile and store in cache. Called from thread pool."""
    key  = (tx, ty, tz)
    path = f"tile_cache/{tz}_{tx}_{ty}.png"

    # Try disk cache first
    if os.path.exists(path):
        try:
            data = open(path, "rb").read()
            surf = pygame.image.load(io.BytesIO(data)).convert()
            with TILE_LOCK:
                TILE_CACHE[key] = surf
            return
        except:
            pass

    # Download
    try:
        url = TILE_URL.format(z=tz, x=tx, y=ty)
        req = urllib.request.Request(url, headers={"User-Agent": "RadarApp/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = r.read()
        with open(path, "wb") as f:
            f.write(data)
        surf = pygame.image.load(io.BytesIO(data)).convert()
        with TILE_LOCK:
            TILE_CACHE[key] = surf
    except:
        with TILE_LOCK:
            TILE_CACHE[key] = "failed"

def request_tile(tx, ty, tz):
    """Return cached surface immediately; schedule download if missing."""
    key = (tx, ty, tz)
    with TILE_LOCK:
        val = TILE_CACHE.get(key)
    if val is None:
        with TILE_LOCK:
            TILE_CACHE[key] = "loading"
        FETCH_POOL.submit(_do_fetch, tx, ty, tz)
        return None
    if isinstance(val, pygame.Surface):
        return val
    return None  # "loading" or "failed"

def pick_tile_zoom(visible_km):
    if   visible_km < 4:  return 16
    elif visible_km < 8:  return 15
    elif visible_km < 16: return 14
    elif visible_km < 30: return 13
    elif visible_km < 60: return 12
    else:                 return 11

def draw_map_tiles(surface, visible_km):
    """Render satellite tiles directly onto surface each frame."""
    tz = pick_tile_zoom(visible_km)

    cx_f, cy_f = deg2tile(MY_LAT, MY_LON, tz)
    cx_i, cy_i = int(cx_f), int(cy_f)

    m_per_px    = 156543.03 * math.cos(math.radians(MY_LAT)) / (2 ** tz)
    km_per_tile = m_per_px * TILE_SIZE / 1000
    # screen pixels per tile at current zoom
    screen_tile_px = int(TILE_SIZE * (RADAR_R / (visible_km * TILE_SIZE / km_per_tile)))

    if screen_tile_px <= 0:
        return

    # fractional offset of center within its tile
    off_x = int((cx_f - cx_i) * screen_tile_px)
    off_y = int((cy_f - cy_i) * screen_tile_px)

    tiles_needed = math.ceil(RADAR_R / screen_tile_px) + 2

    for dx in range(-tiles_needed, tiles_needed + 1):
        for dy in range(-tiles_needed, tiles_needed + 1):
            tx = cx_i + dx
            ty = cy_i + dy
            px = CX + dx * screen_tile_px - off_x
            py = CY + dy * screen_tile_px - off_y

            # skip tiles entirely outside the radar circle
            # (rough check: tile center distance)
            tcx = px + screen_tile_px // 2
            tcy = py + screen_tile_px // 2
            if math.hypot(tcx - CX, tcy - CY) > RADAR_R + screen_tile_px:
                continue

            tile = request_tile(tx, ty, tz)
            if tile:
                if screen_tile_px != TILE_SIZE:
                    tile = pygame.transform.scale(tile, (screen_tile_px, screen_tile_px))
                surface.blit(tile, (px, py))
            else:
                # draw placeholder so it doesn't flash black
                pygame.draw.rect(surface, MISSING_TILE,
                                 (px, py, screen_tile_px, screen_tile_px))

    # darken + green tint overlay
    tint = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
    tint.fill((0, 0, 0, 90))
    surface.blit(tint, (0, 0))
    tint.fill((0, 35, 0, 45))
    surface.blit(tint, (0, 0))

# ── FR24 ─────────────────────────────────────────────────────
fr_api       = FlightRadar24API()
flights_data = []
data_lock    = threading.Lock()
last_update  = [0]

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2)
    return R * 2 * math.asin(math.sqrt(a))

def bearing(lat1, lon1, lat2, lon2):
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(math.radians(lat2))
    y = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2)) -
         math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(dlon))
    return (math.degrees(math.atan2(x, y)) + 360) % 360

def fetch_loop():
    while True:
        try:
            bounds  = fr_api.get_bounds_by_point(MY_LAT, MY_LON, RADIUS_KM * 1000)
            flights = fr_api.get_flights(bounds=bounds)
            results = []
            for f in flights:
                if f.latitude is None or f.longitude is None:
                    continue
                dist = haversine(MY_LAT, MY_LON, f.latitude, f.longitude)
                brng = bearing(MY_LAT, MY_LON, f.latitude, f.longitude)
                results.append({
                    "callsign":  (f.callsign or "???").strip(),
                    "aircraft":  f.aircraft_code or "?",
                    "from":      f.origin_airport_iata or "?",
                    "to":        f.destination_airport_iata or "?",
                    "dist_km":   round(dist, 1),
                    "alt_ft":    f.altitude,
                    "speed_kts": f.ground_speed,
                    "heading":   f.heading or 0,
                    "bearing":   brng,
                    "lat":       f.latitude,
                    "lon":       f.longitude,
                })
            results.sort(key=lambda x: x["dist_km"])
            with data_lock:
                flights_data.clear()
                flights_data.extend(results)
                last_update[0] = time.time()
        except Exception as e:
            print(f"Fetch error: {e}")
        time.sleep(REFRESH_SEC)

threading.Thread(target=fetch_loop, daemon=True).start()

# ── Helpers ───────────────────────────────────────────────────
def draw_plane_icon(surface, x, y, heading, color, size=8):
    h     = math.radians(heading - 90)
    tip   = (x + math.cos(h) * size,                 y + math.sin(h) * size)
    left  = (x + math.cos(h + 2.4) * size * 0.6,     y + math.sin(h + 2.4) * size * 0.6)
    right = (x + math.cos(h - 2.4) * size * 0.6,     y + math.sin(h - 2.4) * size * 0.6)
    tail  = (x + math.cos(h + math.pi) * size * 0.5,  y + math.sin(h + math.pi) * size * 0.5)
    pygame.draw.polygon(surface, color, [tip, left, tail, right])

def draw_text_shadow(surface, text, font, color, x, y):
    surface.blit(font.render(text, True, (0, 20, 0)), (x+1, y+1))
    surface.blit(font.render(text, True, color), (x, y))

def plane_screen_pos(f, visible_km):
    dist  = haversine(MY_LAT, MY_LON, f["lat"], f["lon"])
    ratio = dist / visible_km
    angle = math.radians(f["bearing"] - 90)
    return (int(CX + math.cos(angle) * ratio * RADAR_R),
            int(CY + math.sin(angle) * ratio * RADAR_R),
            dist)

# ── Pygame init ───────────────────────────────────────────────
pygame.init()
screen     = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("RADAR — Başakşehir")

font_mono  = pygame.font.SysFont("Courier New", 12)
font_small = pygame.font.SysFont("Courier New", 11)
font_big   = pygame.font.SysFont("Courier New", 18, bold=True)
font_title = pygame.font.SysFont("Courier New", 22, bold=True)

# Off-screen surface for map (clipped to circle)
map_surf = pygame.Surface((WIDTH, HEIGHT))

# Circle mask surface
circle_mask = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
circle_mask.fill((0, 0, 0, 0))
pygame.draw.circle(circle_mask, (255, 255, 255, 255), (CX, CY), RADAR_R)

sweep_angle  = 0
selected_idx = [None]
zoom         = [1.0]
ZOOM_MIN, ZOOM_MAX = 0.2, 6.0
clock        = pygame.time.Clock()
fullscreen   = [False]

def toggle_fullscreen():
    fullscreen[0] = not fullscreen[0]
    if fullscreen[0]:
        pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    else:
        pygame.display.set_mode((WIDTH, HEIGHT))

# ── Main loop ─────────────────────────────────────────────────
running = True
while running:
    clock.tick(60)
    sweep_angle = (sweep_angle + 1.2) % 360
    visible_km  = RADIUS_KM / zoom[0]

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            if fullscreen[0]:
                toggle_fullscreen()
            else:
                running = False
        if event.type == pygame.KEYDOWN and event.key == pygame.K_F11:
            toggle_fullscreen()
        if event.type == pygame.MOUSEWHEEL:
            zoom[0] = max(ZOOM_MIN, min(ZOOM_MAX, zoom[0] + event.y * 0.15))
        if event.type == pygame.MOUSEBUTTONDOWN:
            mx, my = pygame.mouse.get_pos()
            with data_lock:
                for i, f in enumerate(flights_data):
                    sx, sy, _ = plane_screen_pos(f, visible_km)
                    if math.hypot(mx - sx, my - sy) < 14:
                        selected_idx[0] = i if selected_idx[0] != i else None
                        break

    screen = pygame.display.get_surface()
    screen.fill(BG)

    # ── Satellite map (rendered each frame) ───────────────────
    map_surf.fill(BG)
    draw_map_tiles(map_surf, visible_km)

    # Clip map to radar circle
    clip = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
    clip.blit(map_surf, (0, 0))
    clip.blit(circle_mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    screen.blit(clip, (0, 0))

    # ── Rings & grid ─────────────────────────────────────────
    for i in range(1, 5):
        r = int(RADAR_R * i / 4)
        pygame.draw.circle(screen, GRID if i == 4 else GRID_DIM, (CX, CY), r, 1)
        lbl = font_small.render(f"{visible_km*i/4:.1f}km", True, GRID)
        screen.blit(lbl, (CX + 4, CY - r - 14))

    pygame.draw.line(screen, GRID_DIM, (CX, CY - RADAR_R), (CX, CY + RADAR_R), 1)
    pygame.draw.line(screen, GRID_DIM, (CX - RADAR_R, CY), (CX + RADAR_R, CY), 1)
    d = int(RADAR_R * 0.707)
    pygame.draw.line(screen, GRID_DIM, (CX-d, CY-d), (CX+d, CY+d), 1)
    pygame.draw.line(screen, GRID_DIM, (CX+d, CY-d), (CX-d, CY+d), 1)

    for lbl, ang in [("N",-90),("E",0),("S",90),("W",180)]:
        a  = math.radians(ang)
        lx = CX + int(math.cos(a) * (RADAR_R + 18))
        ly = CY + int(math.sin(a) * (RADAR_R + 18))
        s  = font_mono.render(lbl, True, GRID)
        screen.blit(s, (lx - s.get_width()//2, ly - s.get_height()//2))

    # ── Sweep ────────────────────────────────────────────────
    for trail in range(60):
        ta    = math.radians((sweep_angle - trail) - 90)
        alpha = int(130 * (1 - trail / 60))
        ex    = CX + int(math.cos(ta) * RADAR_R)
        ey    = CY + int(math.sin(ta) * RADAR_R)
        pygame.draw.line(screen, (0, alpha, int(alpha*0.3)), (CX, CY), (ex, ey), 1)
    sr = math.radians(sweep_angle - 90)
    pygame.draw.line(screen, SWEEP_COLOR, (CX, CY),
        (CX + int(math.cos(sr)*RADAR_R), CY + int(math.sin(sr)*RADAR_R)), 2)
    pygame.draw.circle(screen, GRID, (CX, CY), RADAR_R, 2)

    # ── Home ─────────────────────────────────────────────────
    pygame.draw.circle(screen, HOME_COLOR, (CX, CY), 5)
    pygame.draw.circle(screen, HOME_COLOR, (CX, CY), 11, 1)

    # ── Planes ───────────────────────────────────────────────
    with data_lock:
        snapshot = list(flights_data)

    for i, f in enumerate(snapshot):
        sx, sy, _ = plane_screen_pos(f, visible_km)
        if math.hypot(sx - CX, sy - CY) > RADAR_R:
            continue
        is_sel = (selected_idx[0] == i)
        color  = SELECTED_COLOR if is_sel else PLANE_COLOR
        draw_plane_icon(screen, sx, sy, f["heading"], color)
        lsurf = font_small.render(f["callsign"], True, color)
        screen.blit(lsurf, (sx + 10, sy - 8))
        if is_sel:
            pygame.draw.circle(screen, SELECTED_COLOR, (sx, sy), 15, 1)

    # ── HUD ──────────────────────────────────────────────────
    px, py = 10, 10
    draw_text_shadow(screen, "ADSB VIEWER", font_title, SWEEP_COLOR, px, py); py += 28
    age = int(time.time() - last_update[0]) if last_update[0] else 0

    with TILE_LOCK:
        loading_count = sum(1 for v in TILE_CACHE.values() if v == "loading")
    tile_status = f"  [{loading_count} tiles loading]" if loading_count else ""

    draw_text_shadow(screen, f"LAST UPD: {age}s  |  {len(snapshot)} TARGETS{tile_status}", font_mono, TEXT_COLOR, px, py); py += 18
    draw_text_shadow(screen, f"RANGE: {visible_km:.1f}km  ZOOM: {zoom[0]:.1f}x  [SCROLL TO ZOOM]", font_mono, DIM_TEXT, px, py)

    # Contact list
    list_y = HEIGHT - 20 - min(len(snapshot), 12) * 15
    draw_text_shadow(screen, f"{'#':<3} {'CALLSIGN':<10} {'DIST':>6} {'ALT':>7} {'SPD':>5} {'HDG':>4}",
                     font_small, DIM_TEXT, px, list_y - 14)
    for i, f in enumerate(snapshot[:12]):
        c    = SELECTED_COLOR if selected_idx[0] == i else (TEXT_COLOR if i%2==0 else LABEL_COLOR)
        line = f"{i+1:<3} {f['callsign']:<10} {f['dist_km']:>5.1f}km {f['alt_ft']:>6}ft {f['speed_kts']:>4}kt {f['heading']:>3}°"
        draw_text_shadow(screen, line, font_small, c, px, list_y)
        list_y += 15

    # Detail box
    if selected_idx[0] is not None and selected_idx[0] < len(snapshot):
        f  = snapshot[selected_idx[0]]
        bx, by = WIDTH - 215, HEIGHT - 145
        pygame.draw.rect(screen, (0, 18, 0), (bx, by, 205, 135))
        pygame.draw.rect(screen, SWEEP_COLOR, (bx, by, 205, 135), 1)
        draw_text_shadow(screen, f"► {f['callsign']}", font_big, SELECTED_COLOR, bx+8, by+6)
        for row, text in enumerate([
            f"TYPE : {f['aircraft']}",
            f"ROUTE: {f['from']} → {f['to']}",
            f"DIST : {f['dist_km']} km",
            f"ALT  : {f['alt_ft']} ft",
            f"SPEED: {f['speed_kts']} kts",
            f"HDG  : {f['heading']}°",
        ]):
            draw_text_shadow(screen, text, font_small, TEXT_COLOR, bx+8, by+30+row*16)

    pygame.display.flip()

pygame.quit()
sys.exit()

#https://github.com/Kyax3D
#non-commercial open source use
