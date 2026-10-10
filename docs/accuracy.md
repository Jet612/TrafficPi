# Accuracy: what has been measured

Results are published with each feature (plan.md §7), with what they rest on and what they do not show. Nothing here is a promise: it is what one check on one street found, on the date given.

## Counting and stop-sign passes, on one daylight clip (2026-10-10)

**The clip.** Twelve minutes (17:22:44 to 17:34:44, overcast daylight) of the camera's own view, a three-way stop in a residential street, recorded with `trafficpi-vision record` (yolov8s on the Hailo-8L, 30 frames a second, detections of 0.1 and over kept). The clip itself, 375 MB of a real street, is not in the repository. The first 103 seconds of its detections (boxes, no picture, scores of 0.25 and over, as the live service keeps them) are the replay test `backend/tests/test_replay_real_street.py`, with the profile that was saved on the camera that day.

**How it was checked.** The detection log was replayed (`trafficpi-vision replay`) with the saved profile (two counting lines, one stop sign, the speed calibration): 22 counts and 10 passes at the stop sign. Then, with no help from the model:

1. the frame at the moment of each count and each pass was looked at, with the lines drawn on it;
2. a motion detector (each frame against the median of the clip, in a band along each counting line) listed every stretch of movement over each line, and each was matched to a track and a count;
3. the tracker's list of all 39 confirmed tracks was read through, with where each began and ended and where it was counted;
4. the six frames before each pass at the stop sign (3 seconds, 0.6 s apart) were looked at to see whether the vehicle stood still.

### Counts

| | |
|---|---|
| Counts that show a real vehicle on that line at that moment | **22 of 22** (no false count) |
| Vehicles that crossed the picture | 23 (11 from the right to the left in the far lane, 10 from the left (one of them turning into the side street), 2 more by the side street) |
| Of those, counted | **22 of 23** (96%) |
| Vehicles counted twice | 0 |

The vehicle that was not counted turned out of the side street and drove off to the left along the far lane, so its path crossed neither line: a matter of where the lines are, not of detection. Each direction of the road is counted at its own line (the Left Counter covers the near lane, the Right Counter the far lane; near-lane vehicles pass below the Right Counter's end, by the way it was drawn). One leftward vehicle was counted at the Left Counter (in the reverse direction) and not at the Right Counter, as the others of its direction were; why was not looked into. At the right edge of the picture, where vehicles enter and leave, the tracker made 16 tracks that lasted a tenth of a second or two, one after another with the real one; none was counted, and none shows anywhere but in the list of tracks.

The target of M2 was a count within ±5% of a hand count. Here it is 22 against 23, which is 4.3% short; with 23 vehicles one vehicle is 4.3%, so this is *consistent with* the target and **does not show** it.

### Passes at the stop sign

All ten were vehicles driving from the left, past the stop sign on its post, over the crosswalk and across the stop line.

| At | Vehicle | What the frames show | What the rule said |
|---|---|---|---|
| 3.6 s | white van | moves at a steady pace the whole way | rolled through (slowest 0.74 sizes a second, still 0.3 s) |
| 16.1 s | grey saloon | steady pace | rolled through (1.73, 0.1 s) |
| 99.1 s | black SUV | steady pace | rolled through (0.71, 0.2 s) |
| 292.3 s | grey SUV | stands at the sign for more than a second, then goes | **stopped** (still 1.1 s) |
| 407.5 s | dark blue saloon | steady pace, slower | rolled through (0.55, 0.3 s) |
| 594.9 s | white van | steady pace | rolled through (0.77, 0.2 s) |
| 606.5 s | dark blue SUV | creeps, under 2 mph, for over a second, then goes | rolled through (0.17, **still 0.8 s**) |
| 619.8 s | grey SUV | steady pace | rolled through (1.09, 0.1 s) |
| 623.8 s | black SUV | fast, no slowing | **did not stop** (1.95, 0.1 s) |
| 646.8 s | grey SUV | stands at the sign for more than a second, then goes | **stopped** (still 1.4 s) |

By eye, 9 of the 10 are what the rule said. The tenth, at 606.5 s, is a case on the line: the SUV all but stopped, creeping at under 2 mph (the slowest speed the calibration measured: 0.7 m/s) for more than a second, and the rule's test for "stopped" (moved less than 8% of its own size while still, for 1.0 s) was not met by 0.2 s. A person reviewing it might well call it a stop. That is the kind of pass the Review page is for.

Of the 8 passes flagged as needing a look, 7 are plainly vehicles that did not stop and one is a near stop; the 2 passes called stopped are stops. No vehicle that did not stop was passed as stopped (**recall 7 of 7**; precision 7 of 8, or 8 of 8 if the near stop is counted as the rolling stop the rule called it). On this street, in these twelve minutes, 2 of 10 vehicles stopped.

What this does **not** test: the line between "rolled through" and "did not stop", which is a speed set for each sign on the Setup page (the eye cannot tell 1.7 from 1.95 sizes a second); what the rule does at night (stop signs are only judged in daylight, D42); a vehicle hidden by another (there was almost never one in the picture at a time).

## How fast it runs, in daylight

The same recording, which also writes a clip and a log (the live service writes neither): 21,531 frames in 720 s, **29.9 frames a second** (77 skipped, 0.4%); inference 17.0 ms on average (95th percentile 17.6 ms); the whole loop 33.3 ms a frame (the frame period); 42.3 ms from the sensor's timestamp to the detections (95th percentile 44.6 ms); the process used 0.54 cores (the machine 19% on average), 528 MB at most, and the board went from 72.2 to 74.3 °C, at most 77.1 °C, under the 80 °C where it slows itself down. The night figures of M1 (plan.md §7: 29.9 fps, 17.2 ms) are the same to within the noise: daylight, with its many more detections, costs the loop nothing that can be seen.

## Not measured yet

* **Speeds against a vehicle of known speed.** Not possible on this road (plan.md D61). What is known is in [speed-calibration.md](speed-calibration.md).
* **Other models** on this clip (yolov6n, yolox) and **other conditions** (rain, low sun, glare through the window, night).
* **Busy traffic**, with vehicles overlapping: this street has a vehicle every half a minute.
