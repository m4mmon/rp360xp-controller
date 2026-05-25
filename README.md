This is an attempt at re-creating an application for managing the DigiTech RP360XP on Linux and Windows.

It has been made with the help of Claude Code.

![RP360XP Controller window under Debian](RP360XPController.png)

Not fancy, but working and a little more features than Nexus :)

1. This part is about the connection to the device. You have to choose the appropriate serial port (it should be detected automatically).

2. This part is about system settings. Some were not exposed in the original Windows Nexus software.

3. This is to perform full backups / restores. The program can use backups coming from Nexus. And the files created by the program can be used in Nexus too.

4. This is all about the preset.
   As you can see in the screenshot, all the effects are on the same level, like on the pedal. In Nexus, the Amp/cabinets part was separate.
   - On the top part, you have the name of the preset and if it is user or factory. An orange bullet will show its "dirty" (unsaved) status.
   - As in Nexus, you have the quick store (save), store new (save current settings to another preset #), import and export of presets. The file is compatible with the ones produced by Nexus, and Nexus should be able to read the ones produced by this program.
   - This should not be needed, but there is also a "reload" button that triggers the reading of the current preset. It should not be necessary at all.
   - Once you select a slot, you have access to all the slot's parameters.
   If a category is available (because not all slots are used) you can change the category type (from Distorsion to let's say, Delay).
   You can also change effect within the category.
   You can delete a slot by clicking the "x" in the corner. Note that the firmware prevents from deleting all slots, so there is a minimum of one.
   The slots can be re-ordered using drag&drop.
   - The Stomps part allow to assign and trigger stomp buttons.
   - The Expression part is dedicated to the setup of the expression pedal, and its assignation to any parameter.
   - Same with the LFO.
   - Wah is automatically assigned by the firmware to the Wah effect if present. So apart from managing the ranges, there is not much you can do here.


The UI reacts to whatever happens on the pedal too. If something does not work, it's because I have not seen it :)

You can note I have not implemented the copy / paste feature from Nexus. I find it redundant with the "Store New", Export and Import features.


A complete command line tool exists too. You can use it to automate stuff with the pedal, perform tests, etc.

---

What's next?

I plan to polish a little the UI (for example, provide the units and proper range for the LFO speed), externalize all the strings to allow translations.

I have to think about the connection phase too: as of today, the program reads all the user presets' names, and all the factory presets' names too. Nexus must have a hard-coded list of the factory presets, because it does not read them, speeding up the connection process.

It might also be possible to access the expression pedal calibration data, not 100 % sure about that, but for sure the pedal talks when the calibration occurs.

For now, there is no packaging, you have to use the [instructions](src/README.md) to use it.

But as it is, I find it quite OK.
