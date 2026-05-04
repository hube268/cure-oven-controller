import zipfile
import re
import os
import glob

# ── Auto-find the JAR file ──────────────────────────────────────────────────
search_locations = [
    r"C:\Users\sahuber.DESKTOP-OEVNMMJ\Downloads",
    r"C:\Users\sahuber.DESKTOP-OEVNMMJ\Desktop",
    r"C:\Users\sahuber.DESKTOP-OEVNMMJ\Documents",
    r"D:\\",
    r"C:\\",
]

JAR_PATH = None
for location in search_locations:
    matches = glob.glob(os.path.join(location, "**", "*.jar"), recursive=True)
    if matches:
        JAR_PATH = matches[0]
        print(f"Found JAR: {JAR_PATH}")
        break

if not JAR_PATH:
    print("ERROR: Could not find a .jar file. Please place curecontrol.jar in your Downloads folder.")
    input("Press Enter to exit...")
    exit(1)

# ── Extract strings matching control command keywords ───────────────────────
keywords = [
    'M104', 'M106', 'M107', 'M355', 'M3 ', 'M5 ',
    'fan', 'light', 'heat', 'baud', 'welcome',
    'init', 'connect', 'tty', 'ACM', 'USB',
    'IDLE', 'cure', 'serial', 'command', 'send',
]

output_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "commands_found.txt")
results = []

with zipfile.ZipFile(JAR_PATH, 'r') as z:
    for name in z.namelist():
        try:
            data = z.read(name)
            strings = re.findall(b'[ -~]{5,}', data)
            for s in strings:
                decoded = s.decode('ascii', errors='ignore').strip()
                if any(kw.lower() in decoded.lower() for kw in keywords):
                    results.append(f"[{name}]\n  {decoded}\n")
        except Exception:
            pass

with open(output_file, 'w') as f:
    f.write(f"JAR scanned: {JAR_PATH}\n")
    f.write(f"Total matches: {len(results)}\n")
    f.write("=" * 60 + "\n\n")
    for r in results:
        f.write(r)

print(f"\nDone! Found {len(results)} matches.")
print(f"Results saved to: {output_file}")
input("Press Enter to exit...")
