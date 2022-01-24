python3 -m cProfile -o nftscii.profile nftscii.py "$1" "$2" "$3"
gprof2dot -f pstats nftscii.profile | dot -Tpng -o profile.png
