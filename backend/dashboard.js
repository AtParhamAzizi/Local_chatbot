const menuItems = document.querySelectorAll(".menu-item");
const pageFrame = document.getElementById("pageFrame");

menuItems.forEach(item => {

    item.addEventListener("click", () => {

        // Change page
        const page = item.dataset.page;
        pageFrame.src = page;

        // Remove active state
        menuItems.forEach(menuItem => {
            menuItem.classList.remove("active");
        });

        // Activate clicked item
        item.classList.add("active");

    });

});