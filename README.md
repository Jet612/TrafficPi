# TrafficPi

**AI-powered traffic monitoring and analytics, built with Raspberry Pi.**

TrafficPi is an open-source traffic monitoring system that uses computer vision and edge AI to detect, track, and analyze vehicles in real time.

**Project Status:** In development. The features described below represent the intended architecture and development roadmap.

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
- **Local Processing:** Perform AI inference on the Raspberry Pi without sending continuous video to a cloud AI provider.

---

## How It Works

TrafficPi combines edge AI, computer vision, configurable traffic rules, and a web-based dashboard.

### 1. Camera Capture

A connected camera continuously captures video of the monitored roadway.

The initial hardware target is the Raspberry Pi Camera Module 3.

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
| Stop Line | Define where vehicles must stop for stop-sign or traffic-light monitoring. |
| Lane Zone | Separate traffic into individual lanes and directions. |
| Traffic Sign Marker | Identify a sign's location and associate it with a monitoring rule. |
| Speed Calibration | Configure real-world measurements and perspective correction. |
| Exclusion Zone | Ignore irrelevant areas, such as sidewalks or nearby parking lots. |

Users can drag, resize, reposition, and remove zones through the interface.

Camera configurations will be saved and automatically loaded when the monitoring service restarts.

### Monitoring Modes

TrafficPi will support different monitoring modes that can be configured for each camera.

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

Where recording is enabled, supported events may include snapshots or short video clips for manual review.

### Camera Management

A dedicated settings interface will allow users to configure each camera, including detection modes, monitoring zones, recording preferences, and calibration settings.

The long-term goal is to support multiple camera profiles and, where hardware capacity permits, multiple active camera streams.

---

## Technology Stack

TrafficPi uses Python for computer vision and traffic processing, with a TypeScript-based web interface.

| Layer | Technology |
|---|---|
| Hardware | Raspberry Pi 5 |
| Operating System | Raspberry Pi OS Lite 64-bit |
| Camera | Raspberry Pi Camera Module 3 |
| AI Accelerator | Raspberry Pi AI HAT / Hailo accelerator |
| Camera Capture | Picamera2 / libcamera |
| AI Inference | HailoRT and compatible object-detection models |
| Vehicle Tracking | ByteTrack or Hailo-compatible tracking |
| Image Processing | OpenCV and NumPy |
| Backend | Python and FastAPI |
| Real-Time Updates | WebSockets |
| Database | SQLite, with PostgreSQL as a possible future option |
| Frontend | Next.js and TypeScript |
| UI Components | Tailwind CSS and shadcn/ui |
| Data Visualization | Recharts |
| Package Management | pnpm |
| Remote Access | Cloudflare Tunnel and Cloudflare Access |

The inference implementation will depend on the installed Hailo accelerator model and supported software versions.

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

TrafficPi is intended to support remote dashboard access without requiring users to connect to Tailscale or another VPN.

The recommended deployment method will use Cloudflare Tunnel to securely route browser traffic to the locally hosted dashboard, with Cloudflare Access for authentication and access control.

Public access to camera footage, configuration endpoints, and event recordings will not be enabled by default.

Cloudflare will be an optional deployment choice rather than a requirement for using TrafficPi locally.

---

## Privacy and Accuracy

TrafficPi is intended for traffic research, educational use, and general traffic monitoring.

The project will prioritize local processing and configurable data retention. Aggregate statistics can be retained separately from identifiable footage, and video recording can be disabled.

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

Contributor guidelines and development setup instructions will be added as the project progresses.