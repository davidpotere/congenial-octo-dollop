# A naturalist station for a nearby science class

The plan is to sponsor a similar rig for a classroom near Pine Ridge, and to let the two stations talk to each other: the same sky and the same storms, a few blocks apart.

> **Open questions for you:** which school and class (and the teacher's name), the grade level, and whether the school allows a camera on the property. That last one mostly decides whether Arlo is in the kit.

## Kit (check current prices before buying)

| Piece | Why it earns its place in a classroom |
|---|---|
| WeatherFlow Tempest | One sensor, no moving parts, data every minute, and a free API. Kids can compare readings with Pine Ridge. |
| Haikubox (birds and bats) | Hears birds all day and bats all night, with public data per box. The species list *is* the lesson. |
| Bird Buddy | Photo postcards of every visitor. The most engaging thing in the kit for younger students. |
| Small always-on computer (a Raspberry Pi 5 or a spare Mac mini) | Runs this same software. Schools often prefer it on the guest Wi-Fi. |
| *(optional)* skyward camera | Storm and sunset time-lapses for earth-science units. |

## Classroom version of the software

The same codebase with a classroom config:

- `alerts` set to school hours only, with no Arlo and no home address
- the weekly roundup written for the grade level (the `try_this` field already gives a small observation activity each week)
- a shared "Two Stations" page comparing temperature, rain and species lists between Pine Ridge and the school

## Funding paths to explore

- Direct sponsorship (you) plus a matching ask to the PTO or the town education foundation
- Mass Audubon education programs, and Cornell Lab of Ornithology classroom resources (the citizen-science tie-in with eBird and Project FeederWatch)
- Ask WeatherFlow and Haikubox about education discounts. Both companies are small and science-friendly.
