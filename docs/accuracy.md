# Accuracy: what has been measured

Results are published with each feature, with what they rest on and what they do not show. Nothing here is a promise: it is what one check on one street found, on the date given.

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

The target was a count within ±5% of a hand count. Here it is 22 against 23, which is 4.3% short; with 23 vehicles one vehicle is 4.3%, so this is *consistent with* the target and **does not show** it.

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

What this does **not** test: the line between "rolled through" and "did not stop", which is a speed set for each sign on the Setup page (the eye cannot tell 1.7 from 1.95 sizes a second); what the rule does at night (stop signs are only judged in daylight here, a setting on the Setup page); a vehicle hidden by another (there was almost never one in the picture at a time).

## Which model sees best (2026-11-03)

The service runs yolov11s since this date, and ran yolov8s before it. What was wanted is the model that sees and tracks best, of those that take every frame the camera gives without overheating the board.

**What keeps up.** A model that does not fit on the chip at once sends its parts over the Pi's one PCIe lane for every frame, so these run far below Hailo's published figures (from a PC with four lanes). A frame comes every 33.3 ms.

| Model (Hailo-8L) | Published mAP | Published fps | Here, fps | Here, a frame takes | Every frame? |
|---|---|---|---|---|---|
| yolov6n | 32.4 | 356 | 354 | 8.7 ms | yes |
| yolov8s | 43.9 | 110 | 57 | 16.4 ms | yes |
| **yolov11s** | 45.1 | 92 | 37 | **26 ms** | **yes** |
| yolov8m | 49.2 | 51 | 30.5 | 33.7 ms | only in pairs (45 fps) |
| yolov11m | 49.9 | 35 | 22 | 45.7 ms | barely, in pairs (30.5 fps) |
| yolov7, yolov10b, yolov9c, yolov8l, yolov11l | 49 to 52 | 22 to 37 | 14 to 20 | 50 to 71 ms | no |

**In daylight**, the clip above through each, on the same frames, against what was checked by eye. Every model counted the 22 and followed all 23 moving vehicles, with no false count, so this clip does not separate them much. Parked cars show how steady each one is: "shake" is how far a parked car's ground point wanders, as a share of its height, and each of those cars ought to be one track.

| | Passes as in the table above | Tracks | Tracks on parked cars | Shake |
|---|---|---|---|---|
| **yolov11s** | **10 of 10** | 25 | 7 | 1.5% |
| yolov8s | 9 of 10 | 39 | **4** | **1.2%** |
| yolov8m | 10 of 10 | 27 | 7 | 1.8% |
| yolov11m | 9 of 10 | **23** | 5 | 1.9% |
| yolox_s_leaky | 10 of 10 | 25 | 10 | 3.1% |
| yolov6n, tracks starting at 0.4 | 10 of 10 | 26 | 18 | 6.2% |

The one pass that yolov8s and yolov11m got wrong was a stop of 1.0 s on the clip's compressed frames, right on the rule's limit, and the live run had it at 1.1 s. That is one pass, and no sign of better judgement.

**At night**, 35 minutes (22:19 to 22:54) of the live view, 10 frames a second and compressed, through each. A motion detector that knows nothing of any model listed what moved over the lines, and everything anything disagreed on was looked at in the frames: 15 vehicles passed, and each of the 12 stretches of movement nothing counted was one of those 15, so no vehicle got past them all.

| | Counted, of 15 | Counted twice |
|---|---|---|
| **yolov11s** | **12** | 0 |
| yolox_s_leaky | 11 | 1 |
| yolov8s, live at 30 frames a second | 10 | 0 |
| yolov8s | 9 | 0 |
| yolov8m | 9 (11 with tracks starting at 0.45) | 0 |
| yolov6n, tracks starting at 0.4 | 9 | 1 |
| yolov11m | 8 | 0 |

yolov11s counted every car yolov8s did and three more (on the owner's hand-counted clips of the next section it did not: the two came level). Published accuracy does not say this: the larger models did no better on dark cars, and yolov11m came last. A car counted twice here went over both lines, once at each.

So yolov11s. It is level or better by day, it counted the most at night, and it fits the camera without any change to how frames are handled. yolov8s holds a parked car a little steadier.

**The service, on it.** 15 minutes with the rules, the database and the recorder, on the camera at night: 27,041 frames, 30.01 a second; inference 25.9 ms (95th percentile 26.5 ms); 0.23 cores and 258 MB, against 0.32 cores and about 465 MB for yolov8s that evening; the board from 72 down to 67 °C, where yolov8s had stood at 71.6 °C. The temperature is the Pi's, not the accelerator's, the board was still cooling from other work, and that was a cold night: a hot afternoon has not been tried.

**Weak detections.** The tracker can use detections down to 0.1 to hold a vehicle, and the service gives it those of 0.25 and over. With yolov11s, the lower floor counted one more car at night and two cars twice, so it stays at 0.25.

**What this does not show.** 15 cars at night is a small number, and 3 of them are the whole difference; it was one evening, in replay at a third of the frame rate. Three real stops is few. Rain, low sun and glare were not tried. The 148 evidence clips were tried and given up: they run at 15 frames a second with boxes drawn on them, and yolov8s, which made them, reproduced its own outcome on only 12.

To go back: `trafficpi-vision run --model yolov8s`.

## How fast it runs, in daylight

The same recording, which also writes a clip and a log (the live service writes neither): 21,531 frames in 720 s, **29.9 frames a second** (77 skipped, 0.4%); inference 17.0 ms on average (95th percentile 17.6 ms); the whole loop 33.3 ms a frame (the frame period); 42.3 ms from the sensor's timestamp to the detections (95th percentile 44.6 ms); the process used 0.54 cores (the machine 19% on average), 528 MB at most, and the board went from 72.2 to 74.3 °C, at most 77.1 °C, under the 80 °C where it slows itself down. The night figures measured earlier (29.9 fps, 17.2 ms) are the same to within the noise: daylight, with its many more detections, costs the loop nothing that can be seen.

The same camera at night on 2026-11-03, one after the other (`trafficpi-vision dry-run`):

| | yolov8s, before | yolov8s, now | yolov11s, now |
|---|---|---|---|
| Inference | 17.8 ms | 16.4 ms | 26.4 ms |
| Sensor to detections | 41.7 ms | 40.3 ms | 50.9 ms |
| Processor | 0.26 cores | 0.22 cores | 0.22 cores |
| Memory at most | 436 MB | 427 MB | 250 MB |
| Frames a second | 29.8 | 29.8 | 29.9 |
| Frames skipped, all as it starts | 4 in 60 s | 4 in 60 s | 4 in 120 s |

The frame rate is the camera's. yolov11s takes longer over a frame and still has 7 ms in hand.

## Two models against a hand count (2026-11-05)

Three clips were recorded on 2026-11-04 at the camera's full 30 frames a second: 30 minutes from an hour before sunrise (dark), 30 minutes from half an hour before it (dawn), and 6 minutes 44 seconds at noon. The owner counted the cars in each by hand. yolov11s went over each clip, and yolov8s's log from the recording covers the same minutes; both were replayed with the service's tracker, lines and zones, on detections of 0.25 and over. No car crossed both lines. The hand count is a total for each clip, so every car was then looked at in the frames.

| Clip | Hand count | yolov11s, tracks starting at 0.5 | yolov11s, at 0.45 | yolov8s |
|---|---|---|---|---|
| Dark | 11 | 9 | 10 | 9 |
| Dawn | 12 | 12 | 12 | 12 |
| Noon | 5 | 5 | 5 | 5 |
| **All** | **28** | **26** | **27** | **26** |

Neither model counted anything that was not a car, or any car twice, and each of them tracked all 28. At 0.5, the dark clip's four missed counts are three cars:

* **Neither counted one.** It was picked up past the right line, and passed about 6 pixels (of 720) beyond the top end of the left line, so no model could count it. A left line 15% longer counts it, and counts 7 other cars a second time on the other two clips.
* **yolov11s missed one that yolov8s counted.** A fast car, blurred in the dark: yolov11s saw it from the edge of the picture, but scored it under the 0.5 that starts a track until it was past the line. yolov8s counts it on the same compressed frames, so compression is not the reason.
* **yolov8s missed one that yolov11s counted.** As it left the stop sign its box swelled to nearly twice the car's width, and the track broke at the line.

So yolov11s's tracks start at 0.45. It counts the car it missed, and nothing else changes on these clips: 27 of 28. Starts from 0.45 down to 0.3 all gave 27, and 0.45 changed the fewest tracks. On the 12-minute daylight clip it changes nothing that was checked: the same 22 of 22 counts and the same verdict on all 10 passes, with one more track.

**What this does not show.** It is 28 cars on one morning, the lead over yolov8s is one car, and 0.45 was chosen on that same car. The frames of the night test above are gone, so it could not be tried there. yolov8s's own pass over the compressed frames stopped for heat after 13 minutes of the dark clip and 19 of the dawn one; it counted what the recording's log counted as far as it went, and all 5 at noon, so the table uses the log. The noon clip is short because the recording failed.

## Not measured yet

* **Speeds against a vehicle of known speed.** Not possible on this road. What is known is in [speed-calibration.md](speed-calibration.md).
* **Other conditions** (rain, low sun, glare through the window) and a hot afternoon on yolov11s.
* **Busy traffic**, with vehicles overlapping: this street has a vehicle every half a minute.
