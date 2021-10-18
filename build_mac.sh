pyinstaller playscii_mac.spec
cd dist/Playscii.app/Contents/MacOS/
ln -s libSDL2-2.0.0.dylib libSDL2.dylib
ln -s libSDL2_mixer-2.0.0.dylib libSDL2_mixer.dylib
cd ../../../../
hdiutil create -size 110m -srcfolder dist/Playscii.app/ playscii_mac-`cat version`.dmg

