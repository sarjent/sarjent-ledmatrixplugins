# PGA Tour Logo Setup

The PGA Tour Leaderboard plugin displays the PGA Tour logo at the beginning of the scrolling leaderboard.

## Logo File Location

The plugin expects the PGA Tour logo to be located at:
```
assets/sports/pga_logos/pga_logo.png
```

This follows the same pattern as other sports logos in the LEDMatrix project (e.g., `assets/sports/nba_logos/`, `assets/sports/nfl_logos/`).

## Logo Requirements

- **Format**: PNG with transparency (RGBA)
- **Size**: Will be automatically resized to fit the display (max 20px wide, height-4px tall)
- **Recommended dimensions**: 20x28 pixels or similar aspect ratio
- **Background**: Transparent
- **Content**: PGA Tour logo mark or wordmark

## Where to Get the Logo

1. **Official PGA Tour website**: https://www.pgatour.com (look for their press/media kit)
2. **ESPN CDN**: The ESPN API often includes logo URLs in their responses
3. **Sports Logos**: Various sports logo repositories online

## Installation on Raspberry Pi

### Option 1: Manual Upload

1. SSH into your Raspberry Pi
2. Create the directory:
   ```bash
   cd /path/to/LEDMatrix
   mkdir -p assets/sports/pga_logos
   ```
3. Upload the logo file:
   ```bash
   # From your computer (use SCP or similar)
   scp pga_logo.png pi@your-pi-ip:/path/to/LEDMatrix/assets/sports/pga_logos/
   ```
4. Set permissions:
   ```bash
   chmod 644 assets/sports/pga_logos/pga_logo.png
   ```

### Option 2: Download from URL

If you have a URL for the PGA Tour logo:
```bash
cd /path/to/LEDMatrix/assets/sports/pga_logos
wget https://example.com/pga-logo.png -O pga_logo.png
chmod 644 pga_logo.png
```

## Fallback Behavior

If the logo file is not found:
- The plugin will log a warning: "PGA Tour logo not found at ..."
- The scrolling display will still work, but without the logo
- Only the leaderboard text will scroll

## Example Logo Creation

If you need to create a simple logo placeholder:

```python
from PIL import Image, ImageDraw, ImageFont

# Create a simple PGA text logo
img = Image.new('RGBA', (20, 28), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# Draw PGA text (you'll need a font)
# This is just a placeholder - use actual PGA Tour logo for production
draw.text((2, 14), "PGA", fill=(255, 255, 255, 255))

img.save('pga_logo.png')
```

## Verifying Logo Display

After installing the logo:

1. Restart the LEDMatrix service:
   ```bash
   sudo systemctl restart ledmatrix
   ```

2. Check the logs for confirmation:
   ```bash
   tail -f /var/log/ledmatrix/ledmatrix.log | grep -i "pga.*logo"
   ```

3. You should see: `Loaded PGA Tour logo from assets/sports/pga_logos/pga_logo.png`

## Troubleshooting

**Logo not appearing:**
- Check file exists: `ls -la assets/sports/pga_logos/pga_logo.png`
- Check permissions: `chmod 644 assets/sports/pga_logos/pga_logo.png`
- Check logs: `grep "pga.*logo" /var/log/ledmatrix/ledmatrix.log`
- Verify file format: `file assets/sports/pga_logos/pga_logo.png` (should say "PNG image data")

**Logo too large/small:**
- The plugin automatically resizes logos
- Max width: 20 pixels
- Max height: display_height - 4 pixels (usually 28px for 64x32 displays)
- If still not right, manually resize with: `convert pga_logo.png -resize 20x28 pga_logo.png`
