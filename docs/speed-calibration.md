# Calibrating speeds

TrafficPi can say how fast a vehicle was going, in miles or kilometres an hour, once the camera has been told how big a piece of the road is. This page is how to tell it, what you get, and how to find out how far to trust it. The design and its reasons are in [plan.md](../plan.md) (D44 to D46); this page is the practice.

**What it is not.** It is not a certified speed-measurement device, and a speed it gives must not be used as evidence against anyone. Until a calibration has been checked against vehicles of a known speed (the last section), every page that shows a speed says so, in the words "has not yet been checked against a vehicle of known speed". Before a calibration is drawn there are only the rough estimates of the Data page's stop-sign section, which are good to about 40% and say "about" and "estimate".

## What you need

A stretch of road in the camera's picture that is **flat**, and two numbers about a rectangle on it:

* its **width**, across the road, and
* its **length**, along the road.

A third number is worth having: the distance between two other points on the road, anywhere you know it. It is used to test the calibration, not to make it.

## Choosing the rectangle

Draw it on the road, with its corners on things you can see in the picture and find on a map: the ends of a painted stop line or a crosswalk, a kerb corner, the edge of a driveway, a manhole cover, the end of a painted dash.

**Make it as big as the road allows.** The camera sees the road at a slant, so a small error in where a corner is drawn (a few pixels) changes the metres more, the further a point is from the rectangle. TrafficPi works out how much, for every part of the picture, by shaking the corners by 3 pixels and each length by 2% a few hundred times and looking at what happens to the length of 6 m of road at each place. It gives a speed only where it expects to be right to within 10%, and shades that part of the picture while you edit. In a simulated camera (a pinhole seven metres over a flat road, vehicles driven past at known speeds through the real tracker) the expected error at the middle of the rectangle came out as:

| Rectangle | Expected error (1 sigma) | Seen in the simulation |
|---|---|---|
| one lane, 3.5 m by 15 m | 6.6% | 3.6% |
| one lane, 3.5 m by 30 m | 6.3% | 4.1% |
| two lanes, 7 m by 15 m | 7.7% | 3.4% |
| two lanes, 7 m by 30 m | 4.4% | 2.9% |

and a rectangle one lane wide is believed along that lane and hardly beside it. These numbers are for a simulated camera and a perfect road; your picture's own numbers are on the Setup page. What the road is really like (not quite flat, the lens, the way a box is drawn round a vehicle) is what the check at the end measures.

**Flat.** A rectangle laid over a hill, or over a road with a strong crown, is wrong in a way no number on the page will say. Use the flat part.

## Measuring without standing in the road

* **A map's ruler.** The satellite view of most map sites has a "measure distance" tool. Put its two points on the same two things you will put the corners on, zoomed in as far as it goes. Expect to be right to about a metre, so the longer the distance, the better in proportion.
* **A tape or a measuring wheel**, along the kerb or along a painted line, when it is safe to be there, or with a helper watching traffic. Never stand in a road to measure it.
* **Figures you know** from the road's plans or the council: use them only if they are for these two points.

Write down the width, the length, and the extra distance, and where each begins and ends. Do not use a "typical" lane width unless you have nothing better; the length matters most, and the width is what shapes the perspective.

## Drawing it

Setup page, **Lines and zones**, then **Add speed calibration**. A rectangle appears with four numbered corners.

1. Drag the corners onto the corners of the rectangle on the road. They go round it, either way. **Corner 1 to corner 2 is the width** (across the road), and **corner 2 to corner 3 is the length** (along it).
2. Pick **Lengths in** metres or feet, and type the width and the length.
3. Look at what the page says: where speeds are given (the green shading and the sentence under the sizes), and, for each counting line and stop line, whether a speed is given there and how well it is expected to be right. A counting line or stop line that shows "no speed is given here" is outside what this calibration reaches: draw the rectangle larger, or nearer.
4. **Add a known distance** for each extra distance you measured: drag its two ends onto the two points and type its length. The page says what the calibrated map finds for it. "Agrees" means within what is expected of the map and of a length you typed. "Does not agree" means a corner, a length or this distance is wrong: look again at the one furthest from where you suspect.
5. **Save.** The camera service reads it at once; speeds begin once a vehicle has been in view for about a second.

Undo, redo, Delete and the arrow keys work as for the other shapes. Deleting the calibration removes its known distances too. Changing the rectangle later (a corner, the width or the length) gives later speeds a different calibration name (eight characters, stored with each speed); the speeds already recorded keep the name they were made under. Only speeds made under the calibration that is saved now are counted: the Speeds section, the fastest speed of each day and the stop-sign sums leave the others out (the Speeds section says how many), and `speed-check` leaves them out unless it is given `--any-calibration`. They stay in the database. Moving a known distance changes no name, since it is not part of the map.

## What the pages show

* **The live view** writes a vehicle's speed after its name when it has one (Labels switched on).
* **The Data page**, Speeds section: the average, the median, the 85th percentile (the speed 85 in 100 vehicles did not exceed), the fastest, a histogram in bars of 5, and the same by counting line and by hour. It says how many of the vehicles counted had a speed. A vehicle has none when it was in view for less than about a second before the line, where the calibration is not believed, or while its box touches the left, right or bottom edge of the picture (the edge cuts the box off).
* **The Data page**, stop-sign section, **the daily email** and **the Stop signs card**: the fastest speed of each day is the fastest *measured* speed when there is one, and only otherwise the old estimate (marked with a ≈ in a table that holds both). A pass records the slowest, the fastest and the last speed measured while it was in the approach zone; whether it counts as a stop is still judged in the vehicle's own sizes, so calibrating changes no verdict and no count.

A speed is the average over the last second, from a straight line fitted through the vehicle's position on the road (the middle of the bottom of its box) and the camera's own clock. It is not the speed at an instant: a vehicle braking hard shows the speed it had half a second earlier.

## Checking it against vehicles of a known speed

This is what turns a figure into one you can rely on. The test is a vehicle driven past at a speed you know.

1. A car with cruise control, or a phone showing GPS speed (a car's speedometer usually reads a few percent high; a phone's GPS speed does not, so use the phone's figure, with cruise control to hold the speed steady). Pick a quiet time.
2. Drive past the camera, across a counting line that is inside the part of the picture where speeds are given, at a steady speed, in each direction the road is used, at three or four speeds (for example 15, 20, 25 and 30 mph, or the speeds you want to be able to rely on), two or three passes at each.
3. Write down, for each pass, **the time the car crossed the line (to the second, on the camera's clock)** and the speed. A file with one pass to a line:

   ```
   # time, speed [, counting line [, note]]
   2026-10-11 14:03:20, 25, Main street, cruise control
   2026-10-11 14:05:41, 25 mph
   14:09:10, 20
   ```

   A speed with no unit is in the units set on the dashboard (`--units mph` or `--units kmh` says otherwise); `40 km/h`, `11 m/s` and `25 mph` say their own. A time with no date is on the day given with `--date` (today by default); a date with no time is refused. A time is on the camera's clock, the Pi's (the Setup page's time zone), unless it ends with an offset of its own (`2026-10-11T18:03:20+00:00`, `2026-10-11 18:03:20Z`). The time to the second is what matters, so note it from a phone that is set by the network.
4. Run, on the Pi:

   ```bash
   cd backend
   uv run trafficpi-admin speed-check /path/to/passes.csv
   ```

   Each pass is matched to the nearest count that has a speed, within 8 seconds (`--tolerance`), on the line named if one is, and only among counts measured under the calibration that is saved (`--any-calibration` takes the others too; the command says how many it left out). A pass with another count as near in time is marked: it may be another vehicle (`--exclude-ambiguous` leaves those out). It prints one row for each pass and then the mean error (the bias; minus means the camera reads slow), the mean and the largest error without regard to sign (in percent and in miles or kilometres an hour), and the root mean square. It changes nothing.

Ten or more passes, at several speeds, are what a band should rest on; the command says so when there are fewer. A bias that is the same at every speed points at a length that was typed wrongly (a rectangle 3% too long reads 3% fast, or slow); a bias that grows with distance from the rectangle, at the far end of the road, points at a corner.

## The error band

Not measured yet. When it has been, this section gets the table that `speed-check` printed, the date, the calibration's name, the rectangle's size, the speeds driven and the number of passes; the pages' sentence "has not yet been checked against a vehicle of known speed" (`MEASURED_CAVEAT` in `backend/trafficpi/core/speed.py` and `dashboard/src/lib/speed.ts`, kept equal by `backend/tests/data/speed_cases.json`) is replaced by the band; and plan.md M6 is marked done.

## When the camera is moved

The rectangle is drawn on the camera's picture, so it is right only while the camera sees what it saw when it was drawn. The camera-drift check watches the corners of the calibration with the rest of the shapes, and the banner and the System page say when the view has moved. Moving the camera by a hand's breadth changes every speed. After moving it, draw the calibration again and repeat the check.
