document.addEventListener('DOMContentLoaded', () => {
  const toggleBtn = document.getElementById('mode-toggle');

  if (localStorage.getItem('dark-mode') === 'enabled') {
    document.body.classList.add('dark-mode');
    if (toggleBtn) toggleBtn.textContent = '☀️';
  }

  if (toggleBtn) {
    toggleBtn.addEventListener('click', () => {
      document.body.classList.toggle('dark-mode');
      const isDark = document.body.classList.contains('dark-mode');
      toggleBtn.textContent = isDark ? '☀️' : '🌙';
      localStorage.setItem('dark-mode', isDark ? 'enabled' : 'disabled');
    });
  }

  const profileToggle = document.getElementById('profile-toggle');
  const profileDropdown = document.getElementById('profile-dropdown');
  if (profileToggle && profileDropdown) {
    profileToggle.addEventListener('click', () => {
      profileDropdown.style.display = profileDropdown.style.display === 'block' ? 'none' : 'block';
    });

    document.addEventListener('click', function (e) {
      if (!profileDropdown.contains(e.target) && !profileToggle.contains(e.target)) {
        profileDropdown.style.display = 'none';
      }
    });
  }

  // Hide result count by default
  const countDisplay = document.getElementById('resultCount');
  if (countDisplay) {
    countDisplay.style.display = 'none';
  }
});

// Toggle popup modal
function toggleFilterModal() {
  const modal = document.getElementById('filterModal');
  if (modal) modal.style.display = modal.style.display === 'block' ? 'none' : 'block';
}

// Close modal on outside click
window.onclick = function (event) {
  const modal = document.getElementById('filterModal');
  if (event.target === modal) modal.style.display = 'none';
};

// Apply filter logic
function applyFilters() {
  const keyword = document.getElementById('searchKeyword').value.toLowerCase();
  const type = document.getElementById('filterType').value;
  const location = document.getElementById('filterLocation').value.toLowerCase();
  const date = document.getElementById('filterDate').value;

  let visibleCount = 0;
  const items = document.querySelectorAll('.item-card');
  const totalCount = items.length;

  items.forEach(card => {
    const cardType = card.dataset.type;
    const cardName = card.dataset.name.toLowerCase();
    const cardDescription = card.dataset.description.toLowerCase();
    const cardDate = card.dataset.date;
    const cardLocation = card.dataset.location.toLowerCase();

    const match =
      (!keyword || cardName.includes(keyword) || cardDescription.includes(keyword)) &&
      (!type || cardType === type) &&
      (!location || cardLocation.includes(location)) &&
      (!date || cardDate === date);

    const nameSpan = card.querySelector('.item-name');
    const descSpan = card.querySelector('.item-description');

    if (keyword) {
      const regex = new RegExp(`(${keyword})`, 'gi');
      nameSpan.innerHTML = nameSpan.textContent.replace(regex, '<mark>$1</mark>');
      descSpan.innerHTML = descSpan.textContent.replace(regex, '<mark>$1</mark>');
    } else {
      nameSpan.innerHTML = nameSpan.textContent;
      descSpan.innerHTML = descSpan.textContent;
    }

    if (match) {
      card.style.display = 'block';
      visibleCount++;
    } else {
      card.style.display = 'none';
    }
  });

  const countDisplay = document.getElementById('resultCount');
  if (countDisplay) {
    countDisplay.textContent = `Showing ${visibleCount} / ${totalCount} items`;
    countDisplay.style.display = visibleCount < totalCount ? 'block' : 'none';
  }

  toggleFilterModal();
}

 