$(function() {
    $('.platform-picker').each(function() {
        var selector = $('<ul />', { class: 'platform-selector' });
        $('.platform-choice', this).eq(0).before(selector);

        var first = true;
        $('.platform-choice', this).each(function() {
            var title = $('<li />', {
                text: $(this).find('.platform-title').text()
            });
            $(this).find('.platform-title').remove();
            this.className.split(' ').forEach(function(cls) {
                if (cls.startsWith('platform--'))
                    $(title).addClass(cls);
            });
            if ($(this).hasClass('platform-noaltname'))
                $(title).addClass('platform-noaltname');
            if (first) {
                first = false;
                title.addClass('selected');
            } else {
                $(this).hide();
            }
            selector.append(title);
        });

        $('li', selector).click(function(event) {
            event.preventDefault();

            const startPosition = $(this).offset();
            const startScroll = window.scrollY;

            $('.platform-selector li').removeClass('selected');
            $('.platform-choice').hide();

            this.className.split(' ').forEach(function(cls) {
                if (cls.startsWith('platform--')) {
                    $('.platform-picker').each(function() {
                        if ($('.platform-selector li.selected', this).length > 0)
                            return;
                        $('.platform-selector li.platform-noaltname.' + cls, this).addClass('selected');
                        $('.platform-choice.platform-noaltname.' + cls, this).show();
                        if ($('.platform-selector li.selected', this).length > 0)
                            return;
                        $('.platform-selector li.' + cls, this).addClass('selected');
                        $('.platform-choice.' + cls, this).show();
                    });
                }
            });

            const newPosition = $(this).offset();
            const scrollOffset = newPosition.top - startPosition.top;

            window.scrollTo({ top: startScroll + scrollOffset, behavior: "instant" });
        });
    });
});

