# Third-Party Assets

## Flaggen-Icons (ui/assets/flags/)

Die Flaggen-Icons für die Sprachauswahl im UI basieren auf den SVGs aus
[flag-icons](https://github.com/lipis/flag-icons) von Panayiotis Lipiridis.

- Lizenz: MIT
- Die Original-SVGs wurden lokal nach PNG gerastert (22×22 / 44×44 @2x,
  Seitenverhältnis 4:3 beibehalten) und liegen als `ui/assets/flags/<sprachcode>.png`
  bzw. `<sprachcode>@2x.png` im Repository (Dateiname = App-interner
  Sprachcode, nicht der Länder-ISO-Code, siehe `ui/app.py:_flag_icon_path`).

```
MIT License

Copyright (c) Panayiotis Lipiridis

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
