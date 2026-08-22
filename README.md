# Pear Lake Wildfire C40983 Animation

This package is the Pear Lake version of the Brunswick GitHub wildfire animation.

## What it does

- Starts the animation on **July 17, 2026**, the discovery date for Pear Lake C40983.
- Uses a wide westward map view so the western fire growth is included.
- Uses cumulative NASA FIRMS hotspot detections in red.
- Uses the same large **dark-blue date** style as the updated Brunswick animation.
- Builds:
  - `pear_lake_fire_animation.mp4`
  - `pear_lake_fire_animation.gif`
  - `latest.png`
  - a GitHub Pages website in `site/`
- The web animation plays once and then **stays on the final frame** until it is closed or Replay is tapped.
- Includes a **Full screen** button.
- Runs automatically at **11:00 PM America/Vancouver**.
- Stops its own daily workflow after **5 completed days** without meaningful hotspot expansion beyond the 500 m tolerance.

## Files to put in GitHub

```text
fire_animation.py
requirements.txt
.gitignore
.github/workflows/pear-lake-fire.yml
```

The program automatically creates `data/`, `frames/`, `site/`, and the animation output files.

## FIRMS key

In GitHub:

**Settings → Secrets and variables → Actions → New repository secret**

Name the secret:

```text
FIRMS_MAP_KEY
```

Paste your NASA FIRMS MAP_KEY as its value.

## Turn on GitHub Pages

In GitHub:

**Settings → Pages → Build and deployment → Source → GitHub Actions**

Then open:

**Actions → Pear Lake Wildfire Animation → Run workflow**

After the workflow finishes, the animation website URL appears in the workflow's **Deploy Pear Lake animation website** step and in the GitHub Pages section of the repository.

## Important

NASA FIRMS points are satellite thermal detections. They are useful for showing fire/hotspot progression but are **not an official BC Wildfire Service fire perimeter**.
