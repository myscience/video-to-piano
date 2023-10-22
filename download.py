from pytube import YouTube
yt = YouTube('https://www.youtube.com/watch?v=UtGNBYegDBc')

print(yt.streams.filter(type='video'))
yt.streams.get_by_itag(137).download()