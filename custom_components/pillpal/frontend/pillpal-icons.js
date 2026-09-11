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
      // Star sub-path is wound opposite to the capsule outline so the default
      // nonzero fill-rule punches it out as a hole, instead of a barely
      // visible tinted overlay at small sidebar sizes.
      return {
        path:
          "M12.71,5.64 A4,4 0 1 1 18.36,11.29 L11.29,18.36 A4,4 0 1 1 5.64,12.71 Z " +
          "M15.54,5.86 L14.89,7.57 L13.06,7.66 L14.49,8.80 L14.01,10.57 L15.54,9.56 L17.06,10.57 L16.58,8.80 L18.01,7.66 L16.18,7.57 Z",
        viewBox: "0 0 24 24",
      };
    },
  };
})();
