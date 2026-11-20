/* Site settings. Rename the brand here, in one place: every [data-brand] element and the page title read it. */
(function () {
  var BRAND = "Wish Labs";
  /* The studio app is on this same domain (wish.plugbook.si/studio/, routed by the load balancer). */
  var STUDIO_URL = "/studio/";
  /* "See a real build": the read-only share link of a finished design, e.g. "/studio/s/<token>" (or the full
     https://wish.plugbook.si/studio/s/<token>). Every "See a real build" button stays hidden while this is empty. */
  var SHARE_URL = "";
  /* The two Buildathon features (talk to make a wish with Gemini Live; each new design's own sound by Lyria) are
     named in ONE block in How it works (index.html, class "story-new"). Keep false until both are live. */
  var NEW_FEATURES = false;

  /* The six example designs. Names, audience chips, one-liners and "you might type" sentences are the
     design agents' copy (studio/landing/index.html); colours are each spec.json "colors". */
  var GADGETS = [
    {
      slug: "desk-bot", name: "Bolt Desk Bot", chip: "For your desk",
      wish: "a little desk robot that says hi when I sit down",
      one: "A little desk robot that notices you sit down, says hi, and keeps the time and your notifications on its screen face.",
      type: "Make me a little desk robot for my gaming setup: a boxy head with a screen face and an antenna that notices when I sit down, says hi, shows the time and my notifications, and beeps when something comes in. White with an orange accent, techy, not a cute animal.",
      colors: { body: "#F2F0EB", accent: "#FF6A13", detail: "#2B2E33" }, glow: "#FF8A3D",
      face: "bot",
      alt: { life: "Bolt, a white desk robot with an orange chest panel and a cartoon face on its screen, next to a keyboard where someone is typing.",
             studio: "Bolt, a white desk robot with a screen face, an antenna and an orange button, on a grey background." }
    },
    {
      slug: "book-light", name: "Folio Book Light", chip: "For readers",
      wish: "a book-shaped lamp that glows and plays rain sounds",
      one: "A forest-green hardback that stands on your bedside, glows warm through its cream page edge and plays audiobooks and rain sounds from a memory card.",
      type: "Make me a bedside lamp shaped like a thick green hardback book whose page edge glows warm, with a speaker for audiobooks and rain sounds and a button on top.",
      colors: { body: "#3B5F4C", accent: "#F2EADB", detail: "#C7B28A" }, glow: "#7FB08F",
      alt: { life: "Folio, a forest-green hardback-shaped lamp glowing warm through its cream page edge on a bedside table, a hand pressing its top button.",
             studio: "Folio, a forest-green lamp shaped like a hardback book with a cream page edge." }
    },
    {
      slug: "love-note", name: "Love Note Heart", chip: "For someone you love",
      wish: "a heart that glows when I send her a voice note",
      one: "A blush-pink heart for her bedside that glows softly when you send a voice note and plays it when she taps the heart.",
      type: "Make a heart-shaped bedside gift for my girlfriend that glows when I send her a message and plays my voice note when she taps it, with our initials A + R engraved on it, in blush pink.",
      colors: { body: "#EDB1BA", accent: "#FFF1E4", detail: "#9B4A5C" }, glow: "#F38FA2",
      alt: { life: "The Love Note Heart, a blush-pink heart engraved A + R, glowing softly on a bedside table as a finger taps it.",
             studio: "The Love Note Heart, a blush-pink heart engraved A + R, with a round glowing window." }
    },
    {
      slug: "pill-reminder", name: "Dose Medicine Reminder", chip: "For grandparents",
      wish: "a big pill that reminds my grandparents to take their tablets",
      one: "A big white-and-mint capsule that shows which tablet to take now, lights up and chimes at the set time, and has one big button to press once the tablet is taken.",
      type: "Make a medicine reminder for my grandparents shaped like a big capsule pill, white and mint, with a colour screen that shows which tablet to take, one big button to press when it's taken, a chime and a spoken reminder at set times, and let the family set the schedule from their phones.",
      colors: { body: "#A6DCCD", accent: "#16897E", detail: "#F3F6F4" }, glow: "#4FC2A9",
      face: "pill",
      alt: { life: "Dose, a white-and-mint capsule showing a pill on its screen, an older hand pressing its big teal button, beside a pill organiser.",
             studio: "Dose, a white-and-mint capsule with a small screen and a big teal button." }
    },
    {
      slug: "shop-greeter", name: "Welcome Chime", chip: "For your shop",
      wish: "a shop sign that chimes when a customer walks in",
      one: "An arched counter sign with your shop's name that glows and plays a welcome chime when a customer walks in.",
      type: "A welcome sign for my shop counter: an arch with my shop's name on it that lights up and plays a welcome chime when a customer walks in. Bright marigold, red and teal, like shop signage.",
      colors: { body: "#F4A81D", accent: "#B3122E", detail: "#0B6E69" }, glow: "#FFB020",
      alt: { life: "The Welcome Chime, a marigold arched sign with a shop name and WELCOME on it, glowing on a shop counter by the doorway.",
             studio: "The Welcome Chime, a marigold arched counter sign with a red name plate and a teal WELCOME plate." }
    },
    {
      slug: "focus-cube", name: "Pomo Focus Cube", chip: "For exam season",
      wish: "a cube I flip to start 25 minutes of study",
      one: "Flip the cube to start 25 minutes of focus; a ring of light counts down the time and a soft chime tells you when to take a break.",
      type: "A focus cube for exam prep: I flip it to start a 25-minute study session, a light ring shows how much time is left and a soft chime tells me when to take a break. Matte charcoal, minimal, one bright ring, no face.",
      colors: { body: "#2F3135", accent: "#FF5A3C", detail: "#C9C4BA" }, glow: "#FF6A4D",
      alt: { life: "Pomo, a charcoal cube with a red ring and the number 25, on a student's desk beside an open notebook.",
             studio: "Pomo, a matte charcoal cube with a red light ring and the number 25." }
    }
  ];

  /* Only a same-site path or an https link is used, so a typo can never become a script URL. */
  var share = /^(\/[^\/\\]|https:\/\/)/.test(SHARE_URL) ? SHARE_URL : "";

  window.SITE = { BRAND: BRAND, STUDIO_URL: STUDIO_URL, SHARE_URL: share, GADGETS: GADGETS };

  document.title = BRAND + " · Wish it. We'll build it.";
  /* Fill brand slots and studio links as soon as the DOM exists (this file loads at the end of <body>). */
  function fill() {
    var els = document.querySelectorAll("[data-brand]");
    for (var i = 0; i < els.length; i++) els[i].textContent = BRAND;
    var labels = document.querySelectorAll("[data-brand-label]");
    for (var j = 0; j < labels.length; j++) labels[j].setAttribute("aria-label", labels[j].getAttribute("data-brand-label").replace("{brand}", BRAND));
    var links = document.querySelectorAll("[data-studio-link]");
    for (var k = 0; k < links.length; k++) links[k].setAttribute("href", STUDIO_URL);
    var forms = document.querySelectorAll("form[data-studio-form]");
    for (var m = 0; m < forms.length; m++) forms[m].setAttribute("action", STUDIO_URL);
    var shareLinks = document.querySelectorAll("a[data-share-link]");
    for (var n = 0; n < shareLinks.length; n++) if (share) shareLinks[n].setAttribute("href", share);
    var shareBlocks = document.querySelectorAll("[data-share]");
    for (var q = 0; q < shareBlocks.length; q++) shareBlocks[q].hidden = !share;
    var news = document.querySelectorAll("[data-new-features]");
    for (var r = 0; r < news.length; r++) news[r].hidden = !NEW_FEATURES;
  }
  fill();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fill);
})();
