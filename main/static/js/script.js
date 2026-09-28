let slideIndex = 1;
let autoPlayTimer;
const carouselSection = document.querySelector('.hero-carousel');

// Initial setup
showSlides(slideIndex);
startAutoPlay();

// --- Core Controls ---
function moveSlide(n) {
    showSlides(slideIndex += n);
    // When user manually clicks, reset the timer for better UX
    resetAutoPlay();
}

function currentSlide(n) {
    showSlides(slideIndex = n);
    resetAutoPlay();
}

// --- Main Display Function ---
function showSlides(n) {
    let i;
    const slides = document.getElementsByClassName("carousel-slide");
    const dots = document.getElementsByClassName("indicator");
    
    // Boundary checks
    if (n > slides.length) { slideIndex = 1 }    
    if (n < 1) { slideIndex = slides.length }
    
    // Deactivate all slides and dots
    for (i = 0; i < slides.length; i++) {
        slides[i].classList.remove("active");  
    }
    for (i = 0; i < dots.length; i++) {
        dots[i].classList.remove("active");
    }
    
    // Activate the current slide and dot
    // This triggers the CSS animations defined in style.css
    if (slides.length > 0) {
        slides[slideIndex-1].classList.add("active");  
        dots[slideIndex-1].classList.add("active");
    }
}

// --- Best UX: Auto-play with Pause on Hover ---
function startAutoPlay() {
    autoPlayTimer = setInterval(() => {
        moveSlide(1);
    }, 7000); // Premium designs use slower, 7-second intervals
}

function stopAutoPlay() {
    clearInterval(autoPlayTimer);
}

function resetAutoPlay() {
    stopAutoPlay();
    startAutoPlay();
}

// Event Listeners for Hover
if (carouselSection) {
    carouselSection.addEventListener('mouseenter', stopAutoPlay);
    carouselSection.addEventListener('mouseleave', startAutoPlay);
}




document.addEventListener('DOMContentLoaded', function() {
    const navItems = document.querySelectorAll('.service-nav-item');
    const displayImages = document.querySelectorAll('.display-img');
    const captionText = document.getElementById('caption-text');

    navItems.forEach(item => {
        item.addEventListener('click', function() {
            // 1. Remove 'active' class from all nav items
            navItems.forEach(nav => nav.classList.remove('active'));
            
            // 2. Add 'active' to the clicked item
            this.classList.add('active');

            // 3. Get the specific image ID and caption from data attributes
            const targetId = 'img-' + this.getAttribute('data-img');
            const newCaption = this.getAttribute('data-caption');

            // 4. Hide all images
            displayImages.forEach(img => {
                img.classList.remove('active');
            });

            // 5. Show the target image with a smooth fade
            const activeImg = document.getElementById(targetId);
            if (activeImg) {
                activeImg.classList.add('active');
            }

            // 6. Update caption text with a slight delay for effect
            captionText.style.opacity = '0';
            setTimeout(() => {
                captionText.innerText = newCaption;
                captionText.style.opacity = '1';
            }, 300);
        });
    });
});