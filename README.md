# TrafficPi

**AI-powered traffic monitoring and analytics, built with Raspberry Pi.**

TrafficPi is an open-source traffic monitoring system that uses computer vision and edge AI to detect, track, and analyze vehicles in real time.

**Project Status:** In development, and running on one Raspberry Pi 5 with a Hailo-8L and a Camera Module 3. It counts vehicles at counting lines, watches stop signs (a picture and a short clip of every pass that needs a look), keeps the paths vehicles took, and shows all of it in a password-protected dashboard that also reports on the machine's health. It can e-mail a daily report of how the stop signs did (built, and tried with a stand-in sender; not yet with a real mail provider's key). Speed estimation and a release are not done. [docs/install.md](docs/install.md) takes a blank Raspberry Pi OS Lite card to a working system, but has not yet been tried on a clean image. See [plan.md](plan.md) for the roadmap and [todo.md](todo.md) for current tasks.

---

## Overview

TrafficPi processes video from a connected camera using AI-powered object detection and vehicle tracking. It transforms camera footage into useful traffic statistics, including vehicle counts, traffic flow, estimated vehicle speeds, and potential traffic violations.

TrafficPi is designed to be configurable rather than hardcoded for one intersection.

Users will be able to customize their camera configuration directly from a web dashboard, defining road boundaries, monitoring zones, virtual stop lines, and traffic rules without writing code.

### Key Features

- **Real-Time Vehicle Detection:** Detect and classify cars, trucks, buses, motorcycles, and other supported objects.
- **Vehicle Tracking:** Track individual vehicles across frames to estimate movement and prevent duplicate counting.
- **Traffic Analytics:** Monitor traffic volume, directions, vehicle classifications, and trends over time.
- **Speed Estimation:** Estimate vehicle speeds using camera calibration and real-world distance measurements.
- **Stop-Sign Monitoring:** Identify vehicles that may have failed to stop at a configured stop line.
- **Custom Detection Zones:** Visually configure roads, lanes, intersections, and monitoring areas.
- **Web Dashboard:** View live traffic statistics, historical trends, camera feeds, and recorded events.
- **Remote Access:** Securely access the dashboard from outside the local network.
- **Daily Email Report:** Optionally e-mail a short report each day, with the number of vehicles that did not stop at the stop signs as its headline (numbers only; no pictures or clips are sent).
- **Local Processing:** Perform AI inference on the Raspberry Pi without sending continuous video to a cloud AI provider.

---

## How It Works

TrafficPi combines edge AI, computer vision, configurable traffic rules, and a web-based dashboard.

### 1. Camera Capture

A connected camera continuously captures video of the monitored roadway.

The initial hardware target is the Raspberry Pi Camera Module 3. Recorded video files (for testing and calibration) and network cameras may be supported as additional sources later.

### 2. AI Object Detection

Video frames are processed using a Hailo AI accelerator and a compatible object-detection model.

The system detects supported vehicle classes and identifies their positions within the frame.

### 3. Vehicle Tracking

Detected objects are assigned temporary tracking IDs.

By following vehicles across consecutive frames, TrafficPi can determine their trajectories, movement directions, approximate speeds, and interactions with configured monitoring zones.

### 4. Traffic Rule Processing

Tracked vehicles are evaluated against the rules configured for the camera.

Examples include:

- Counting vehicles crossing a virtual line.
- Measuring travel time between calibrated roadway locations.
- Checking whether vehicles stop before a configured stop line.

Each monitoring rule can be enabled or disabled independently.

### 5. Data Storage

TrafficPi stores traffic statistics and detected events locally.

Recorded data may include timestamps, vehicle classifications, directions, estimated speeds, event types, and optional evidence snapshots or short video clips.

### 6. Dashboard and Analytics

A web dashboard retrieves processed traffic information through the backend API.

Users can review live monitoring data, explore historical traffic patterns, configure cameras, and manage detection rules.

---

## Customizable Camera Configuration

**Customization is a core part of TrafficPi.**

Every camera installation is different. Camera height, viewing angle, road layout, traffic controls, and monitoring goals all affect how traffic should be analyzed.

Rather than relying on predefined intersection layouts, TrafficPi will provide a visual configuration editor.

### Visual Zone Editor

Users will be able to open a camera preview and configure monitoring areas by drawing directly on the image.

Supported configuration elements are planned to include:

| Element | Purpose |
|---|---|
| Detection Zone | Define where objects should be detected and monitored. |
| Counting Line | Count vehicles crossing a virtual line in a configured direction. |
| Stop Line | Define where vehicles must stop for stop-sign monitoring. |
| Lane Zone | Separate traffic into individual lanes and directions. |
| Speed Calibration | Configure real-world measurements and perspective correction. |
| Exclusion Zone | Ignore irrelevant areas, such as sidewalks or nearby parking lots. |

Users can drag, resize, reposition, and remove zones through the interface.

Camera configurations will be saved and automatically loaded when the monitoring service restarts.

### Monitoring Modes

TrafficPi will support different monitoring modes that can be configured for each camera. Modes are not mutually exclusive; a camera profile can enable any combination of them.

**Traffic Counting Mode**

Designed for roadways where the primary goal is understanding vehicle movement.

Provides vehicle counts, classifications, travel directions, and hourly traffic trends.

No stop sign or traffic light is required.

**Stop-Sign Monitoring Mode**

Designed for intersections controlled by stop signs.

Users configure an approach zone, stop line, and stopping criteria. TrafficPi then evaluates vehicle movement to identify potential rolling stops or failures to stop.

**Speed Monitoring Mode**

Designed for monitoring roadway speeds.

Users calibrate the camera using known ground distances and road geometry. TrafficPi estimates speeds from tracked vehicle movement and produces speed distributions and average-speed statistics.

Speed measurements are estimates and require calibration and validation.

### Camera Profiles

Each camera will have its own configuration profile containing its source, zones, rules, calibration data, and settings.

For example:

- **Residential Intersection:** Vehicle counting and stop-sign monitoring.
- **Main Road:** Vehicle counting, direction tracking, and speed estimation.
- **Parking Entrance:** Vehicle counting and entry/exit tracking.

Profiles are intended to be reusable, with import/export support planned for sharing configurations and backing up settings.

This allows the same TrafficPi software to serve different locations without custom code changes.

---

## Web Dashboard

TrafficPi will include a responsive web interface built with modern web technologies.

### Dashboard Overview

The main dashboard will display important metrics, including:

- Total vehicles detected today
- Vehicles detected per hour
- Average estimated vehicle speed
- Vehicle classification breakdown
- Suspected traffic violation counts
- Traffic direction distribution
- Camera and AI processor status

Interactive charts will allow users to analyze trends over selectable date ranges.

The **Data** page does this for the stop signs and the traffic. Over today, 7, 30 or 90 days, or all time, it shows the share of vehicles that stopped, rolled through, did not stop or could not be told, and how far each share moved since the days before; the busiest hour and day, and the hours and kinds of vehicle with the lowest and highest stop rate; charts by hour of the day, day, day of the week, kind of vehicle and stop sign (each also as a table); how long a typical stop lasts and how slowly a typical roll goes; how many of the passes you have looked at were false alarms; and the vehicles counted, by hour, day, kind and counting line.

### Live Camera View

The dashboard will provide a camera preview with optional overlays for:

- Vehicle bounding boxes
- Tracking IDs
- Detection and counting zones
- Stop lines
- Lane boundaries
- Estimated vehicle speeds

Users can toggle visual overlays on or off.

### Events and History

Detected events will be stored for review.

Users will be able to filter events by date, camera, event type, and vehicle classification.

Where recording is enabled, supported events may include snapshots or short video clips for manual review. For stop signs, a vehicle that rolled through or did not stop gets a picture and a clip of about nine seconds around the moment it crossed the stop line, which you can watch and then confirm or call a false alarm; a vehicle that stopped gets none. The pictures are kept for 30 days or 2 GB unless you choose otherwise, but the clips of a rolled-through stop only for 3 hours and those of a vehicle that did not stop for 2 days (the counts stay). All of it can be switched off, and it is only as sharp as the detector's own picture (640 by 360 pixels), which is usually too small to read a number plate or a face (one very close to the camera can still be readable, so look at a few before you rely on that).

### Trails

TrafficPi will save the path every moving vehicle takes through the picture: a few hundred bytes of points, with no video, no face and no number plate. The History page will show a day, or any hours of it, as paths or as a heat map drawn over the camera picture, filtered by kind of vehicle, with the counting lines and zones on top. It will play a day hour by hour and save the picture. It shows how the road is really used, and whether the lines and zones fit it. Trails are kept for a year unless you choose otherwise.

### Camera Management

A dedicated settings interface will allow users to configure each camera, including detection modes, monitoring zones, recording preferences, and calibration settings.

Each camera has its own profile. The first releases target a single active camera; running several camera streams at once is a long-term goal, where hardware capacity permits.

---

## Technology Stack

TrafficPi uses Python for computer vision and traffic processing, with a TypeScript-based web interface.

| Layer | Technology |
|---|---|
| Hardware | Raspberry Pi 5 |
| Operating System | Raspberry Pi OS Lite 64-bit (Debian 13, Trixie) |
| Camera | Raspberry Pi Camera Module 3 |
| AI Accelerator | Raspberry Pi AI HAT+ (Hailo-8L, 13 TOPS, or Hailo-8, 26 TOPS) |
| Camera Capture | Picamera2 / libcamera |
| AI Inference | HailoRT and compatible pre-compiled object-detection models |
| Vehicle Tracking | ByteTrack or Hailo-compatible tracking |
| Image Processing | OpenCV and NumPy |
| Backend | Python and FastAPI |
| Real-Time Updates | WebSockets |
| Database | SQLite, with PostgreSQL as a possible future option |
| Frontend | Next.js and TypeScript |
| UI Components | Tailwind CSS and shadcn/ui |
| Data Visualization | Recharts |
| Frontend Packages | pnpm |
| Remote Access | Cloudflare Tunnel and Cloudflare Access |

Pre-compiled models are specific to the accelerator variant, so the inference implementation depends on the installed Hailo accelerator and supported software versions. The Raspberry Pi AI HAT+ 2 (Hailo-10H) uses a different runtime and is not targeted initially.

---

## System Architecture

TrafficPi is designed around three primary components.

**Vision Service**

Responsible for camera capture, accelerated AI inference, vehicle tracking, geometry calculations, and traffic event detection.

**Backend API**

Responsible for configuration management, event storage, traffic statistics, and communication with the web dashboard.

**Web Dashboard**

Responsible for visualizing traffic data, configuring camera installations, managing rules, and presenting historical analytics.

The processing pipeline follows this structure:

**Camera → AI Detection → Vehicle Tracking → Configurable Rule Engine → Database → API → Dashboard**

The vision service runs locally on the Raspberry Pi. The dashboard can be accessed on the local network or remotely through an authenticated web connection.

---

## Remote Access

On the local network the dashboard is at `http://<the Pi>.local:8000`; everything needs the login. For access from outside, [docs/remote-access.md](docs/remote-access.md) describes two ways: a VPN such as Tailscale (nothing is exposed), or a Cloudflare Tunnel with Cloudflare Access in front (viewers need no VPN). Cloudflare's terms let it limit video on its network, so viewers who come through it get a still picture renewed every few seconds instead of the live video unless the owner switches that on in Settings. The Cloudflare route is written from its documentation and has not been tried with a real account yet.

Nothing is public by default, and Cloudflare is an option, not a requirement. What is and is not protected is in [docs/security.md](docs/security.md).

---

## Privacy and Accuracy

TrafficPi is intended for traffic research, educational use, and general traffic monitoring.

The project will prioritize local processing and configurable data retention. Aggregate statistics can be retained separately from identifiable footage, and video recording can be disabled.

TrafficPi does not read license plates and does not recognize faces or identify individual people or vehicles. It classifies road users by kind (car, truck, bus, motorcycle, bicycle, person) so that it can count them. Looking for people at all can be switched off in the dashboard's Settings, and then they get no box, no path and no count.

TrafficPi is not intended to function as a certified speed enforcement system or automatically issue traffic citations.

Speed estimates and suspected violations may be inaccurate due to camera perspective, occlusion, lighting, tracking failures, signal visibility, or calibration errors. Events requiring a legal or safety determination should be reviewed independently.

Users are responsible for complying with applicable recording, surveillance, and privacy laws.

---

## Project Goals

TrafficPi is being developed as both a practical traffic analytics tool and an open-source software engineering project.

The main goals are to:

1. Build a reliable end-to-end computer vision application using accessible hardware.
2. Make traffic analytics configurable enough to support a variety of road layouts.
3. Provide an intuitive interface that does not require users to modify source code.
4. Keep core detection and processing local to the device.
5. Make installation, configuration, and troubleshooting approachable for other developers.
6. Develop a modular architecture that can grow with additional AI models, camera sources, and monitoring capabilities.

---

## Contributing

TrafficPi is intended to become an open-source project welcoming contributions, bug reports, feature ideas, and improvements.

Installing is in [docs/install.md](docs/install.md) and running it in [docs/operations.md](docs/operations.md). Development machine setup is documented in [docs/dev-setup.md](docs/dev-setup.md). Contributor guidelines will be added as the project progresses.

---

## License

TrafficPi is released under the [MIT License](LICENSE).
