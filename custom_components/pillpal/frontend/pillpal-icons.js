// Registers the Pill★Pal capsule-and-star mark as a custom ha-icon prefix
// ("pillpal:logo") so the sidebar can show the real brand icon instead of a
// generic MDI symbol. See https://github.com/ToPa451/PillPal/issues/3
(() => {
  if (!window.customIcons) {
    window.customIcons = {};
  }
  window.customIcons.pillpal = {
    getIcon: async (iconName) => {
      if (iconName !== "logo") {
        throw new Error(`Unknown pillpal icon: ${iconName}`);
      }
      return {
        path: "M12.71,5.64 A4,4 0 1 1 18.36,11.29 L11.29,18.36 A4,4 0 1 1 5.64,12.71 Z",
        secondaryPath:
          "M15.54,6.16 L16.09,7.70 L17.72,7.75 L16.44,8.76 L16.89,10.33 L15.54,9.41 L14.18,10.33 L14.63,8.76 L13.35,7.75 L14.98,7.70 Z",
        viewBox: "0 0 24 24",
      };
    },
  };
})();
