import os
import subprocess

os.makedirs("videos", exist_ok=True)

command = [
    "ffmpeg", "-y",
    "-f", "lavfi",
    "-i", "color=c=black:s=1920x1080:d=12",
    "-vf",
    (
        "noise=alls=18:allf=t,"
        "drawbox=x=0:y=0:w=1920:h=1080:color=black@0.25:t=fill,"
        "drawtext=text='THE NIGHT EVERYTHING CHANGED':"
        "fontcolor=white:fontsize=64:x=(w-text_w)/2:y=300,"
        "drawtext=text='Inside the Case':"
        "fontcolor=gray:fontsize=38:x=(w-text_w)/2:y=405,"
        "drawtext=text='A quiet street. One missing person. No answers.':"
        "fontcolor=white:fontsize=36:x=(w-text_w)/2:y=760,"
        "zoompan=z='min(zoom+0.0015,1.12)':d=300:s=1920x1080,"
        "fade=t=in:st=0:d=1,fade=t=out:st=11:d=1"
    ),
    "-r", "25",
    "videos/moving_scene.mp4"
]

subprocess.run(command, check=True)
print("Video gemaakt: videos/moving_scene.mp4")