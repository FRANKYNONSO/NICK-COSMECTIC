// Storefront JS — cart/checkout interactivity will be added in Step 2.
console.log("Storefront loaded");

// Mobile nav toggle
document.addEventListener("DOMContentLoaded", () => {
  const toggle = document.querySelector(".nav-toggle");
  const collapsible = document.getElementById("primary-nav");

  if (toggle && collapsible) {
    toggle.addEventListener("click", () => {
      const isOpen = collapsible.classList.toggle("open");
      toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
    });
  }
});
