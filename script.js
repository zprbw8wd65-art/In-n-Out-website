// Mobile nav toggle
document.addEventListener('DOMContentLoaded', function(){
  var toggle = document.querySelector('.nav-toggle');
  var links = document.querySelector('.navlinks');
  if(toggle && links){
    toggle.addEventListener('click', function(){
      links.classList.toggle('open');
      var expanded = links.classList.contains('open');
      toggle.setAttribute('aria-expanded', expanded);
    });
  }

  // Inventory filtering (only present on inventory.html)
  var grid = document.getElementById('vehicle-grid');
  if(grid){
    var makeSel = document.getElementById('filter-make');
    var priceSel = document.getElementById('filter-price');
    var bodySel = document.getElementById('filter-body');
    var countEl = document.getElementById('results-count');
    var cards = Array.prototype.slice.call(grid.querySelectorAll('.tag'));

    function applyFilters(){
      var make = makeSel.value;
      var maxPrice = priceSel.value ? parseInt(priceSel.value, 10) : null;
      var body = bodySel.value;
      var visible = 0;

      cards.forEach(function(card){
        var cMake = card.dataset.make;
        var cPrice = parseInt(card.dataset.price, 10);
        var cBody = card.dataset.body;
        var show = true;
        if(make && cMake !== make) show = false;
        if(maxPrice && cPrice > maxPrice) show = false;
        if(body && cBody !== body) show = false;
        card.style.display = show ? '' : 'none';
        if(show) visible++;
      });

      if(countEl){
        countEl.textContent = visible + (visible === 1 ? ' vehicle matches your search' : ' vehicles match your search');
      }
    }

    [makeSel, priceSel, bodySel].forEach(function(el){
      if(el) el.addEventListener('change', applyFilters);
    });

    // Run once on page load so the count reflects the real number of
    // cards actually on the page, not a leftover placeholder number.
    applyFilters();
  }
});
