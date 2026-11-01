// Bedtime story robot: parametric shell for the price simulation.
// Two printed parts: the hollow shell (head + body, open at the bottom) and a flat base plate.
// Hidden functional geometry follows blueprint §5: fixed eye window for the 0.96" OLED,
// speaker grille, chest light window, top "story" button, rear USB-C port, four insert bosses.
// Usage: openscad -D 'part="shell_print"' -o shell.stl bedtime_robot.scad
//        part = "shell" | "shell_print" (upside down, as printed) | "plate" | "assembly"

part = "assembly";
$fn = 48;

wall = 2.2;
// body (lower box)
bw = 80; bd = 68; bh = 58; br = 12;
// head (upper box), overlaps the body by 6 mm
hw = 92; hd = 78; hh = 72; hr = 16; hz = 52;
// ears on the head sides
ear_r = 9; ear_len = 8;
// interfaces (keep in sync with spec/bedtime-robot.json)
eye_w = 24; eye_h = 13;          // OLED active-area window
button_d = 14;                   // big top button for the child
chest_d = 16;                    // night-light diffuser window
grille_d = 3; grille_pitch = 6;  // speaker grille
usb_w = 12; usb_h = 6;           // USB-C opening
boss_od = 8; boss_id = 4.0; boss_h = 12; boss_inset = 6;
plate_t = 2.5; plate_clear = 0.3;

module rbox(w, d, h, r) {
  hull() for (x = [-w/2 + r, w/2 - r], y = [-d/2 + r, d/2 - r], z = [r, h - r])
    translate([x, y, z]) sphere(r);
}

module outer() {
  // body: flat bottom at z = 0
  intersection() {
    translate([0, 0, -br]) rbox(bw, bd, bh + br, br);
    translate([-200, -200, 0]) cube([400, 400, 400]);
  }
  translate([0, 0, hz]) rbox(hw, hd, hh, hr);
  for (s = [-1, 1])
    translate([s * (hw/2 + ear_len/2 - 2), 0, hz + hh/2]) rotate([0, 90, 0]) cylinder(r = ear_r, h = ear_len + 4, center = true);
}

module cavity() {
  // open bottom: inner body runs below z = 0
  translate([0, 0, -br - 5]) rbox(bw - 2*wall, bd - 2*wall, bh + br + 5 - wall + 6, br - wall);
  translate([0, 0, hz + wall]) rbox(hw - 2*wall, hd - 2*wall, hh - 2*wall, hr - wall);
}

module openings() {
  // eyes window on the head front
  translate([0, -hd/2, hz + hh/2 + 4]) cube([eye_w, 4*wall, eye_h], center = true);
  // story button on the head top
  translate([0, 0, hz + hh - wall - 1]) cylinder(d = button_d, h = 4*wall);
  // chest light window
  translate([0, -bd/2, 40]) rotate([90, 0, 0]) cylinder(d = chest_d, h = 4*wall, center = true);
  // speaker grille, hex pattern
  for (i = [-2:2], j = [-1:1]) {
    x = i * grille_pitch + (j % 2 == 0 ? 0 : grille_pitch/2);
    z = 18 + j * grille_pitch * 0.87;
    if (abs(x) < 14) translate([x, -bd/2, z]) rotate([90, 0, 0]) cylinder(d = grille_d, h = 4*wall, center = true);
  }
  // USB-C at the back, low
  translate([0, bd/2, 8]) cube([usb_w, 4*wall, usb_h], center = true);
}

function boss_xy() = [for (sx = [-1, 1], sy = [-1, 1])
  [sx * (bw/2 - wall - boss_inset), sy * (bd/2 - wall - boss_inset)]];

module bosses() {
  for (p = boss_xy()) translate([p[0], p[1], plate_t]) difference() {
    union() {
      cylinder(d = boss_od, h = boss_h - plate_t);
      // web to the nearest corner so the boss is fused to the wall
      hull() { cylinder(d = boss_od * 0.6, h = boss_h - plate_t); translate([sign(p[0]) * boss_inset, sign(p[1]) * boss_inset, 0]) cylinder(d = boss_od * 0.6, h = boss_h - plate_t); }
    }
    translate([0, 0, -1]) cylinder(d = boss_id, h = boss_h - 2);
  }
}

module shell() {
  difference() { outer(); cavity(); openings(); }
  intersection() { bosses(); outer(); }
}

module plate() {
  difference() {
    translate([0, 0, 0]) linear_extrude(plate_t) offset(r = br - wall - plate_clear) offset(delta = -(br - wall))
      square([bw - 2*wall, bd - 2*wall], center = true);
    for (p = boss_xy()) translate([p[0], p[1], -1]) cylinder(d = 2.8, h = plate_t + 2);
    // vent slots
    for (i = [-3:3]) translate([i * 7 - 1.5, -15, -1]) cube([3, 30, plate_t + 2]);
  }
}

if (part == "shell") shell();
if (part == "shell_print") rotate([180, 0, 0]) translate([0, 0, -(hz + hh)]) shell();
if (part == "plate") plate();
if (part == "assembly") { shell(); color("gray") translate([0, 0, -plate_t - 4]) plate(); }
