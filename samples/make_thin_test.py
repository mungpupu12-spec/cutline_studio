"""A torture-test shape: a solid blob with (a) a very thin whisker/spike
sticking out, and (b) a narrow neck connecting two lobes -- both thinner
than a typical 2mm cut offset. This is exactly the kind of small detail
that naive buffering can silently destroy."""

from PIL import Image, ImageDraw

W, H = 500, 300
img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# Main body
d.ellipse((60, 80, 220, 220), fill=(90, 60, 200, 255))

# Thin whisker sticking out (a few px wide -> sub-mm at typical DPI)
d.polygon([(220, 140), (420, 130), (420, 138), (220, 160)], fill=(90, 60, 200, 255))

# Second lobe connected by a narrow neck
d.ellipse((330, 190, 460, 280), fill=(90, 60, 200, 255))
d.polygon([(230, 205), (335, 220), (335, 235), (230, 220)], fill=(90, 60, 200, 255))

img.save("/root/cutline_studio/samples/thin_test.png")
print("wrote thin_test.png")
