define(function(require){
    'use strict';

    var SegmentList = require('views/segmentList'),
           IdeaList = require('views/ideaList'),
          IdeaPanel = require('views/ideaPanel'),
        MessageList = require('views/messageList'),
     SynthesisPanel = require('views/synthesisPanel'),
                  $ = require('jquery'),
                  _ = require('underscore');

    var groupManager = Marionette.Controller.extend({

        initialize: function(){

           this.stateButton = null;
        },
        /**
         * A locked panel will not react to external UI state changes, such as
         * selecting a new current idea.
         */
        _panelIsLocked: false,

        _unlockCallbackQueue: {},

        isGroupLocked: function(){
          return this._panelIsLocked;
        },

        /**
         * Process a callback that can be inhibited by panel locking.
         * If the panel is unlocked, the callback will be called immediately.
         * If the panel is locked, visual notifications will be shown, and the
         * callback will be memorized in a queue, removing duplicates.
         * Callbacks receive no parameters.
         * If queued, they must assume that they can be called at a later time,
         * and have the means of getting any updated information they need.
         */
        filterThroughPanelLock: function(callback, queueWithId){
            if (!this._panelIsLocked){
                callback();

            } else {
                if(queueWithId){
                    if(this._unlockCallbackQueue[queueWithId]!==undefined){
                    }
                    else{
                       this._unlockCallbackQueue[queueWithId]=callback;
                    }
                }
            }
        },

        /**
         * lock the panel if unlocked
         */
        lockPanel: function(){
            if(!this._panelIsLocked){
                this._panelIsLocked = true;
                this.stateButton.addClass('icon-lock').removeClass('icon-lock-open');
            }
        },

        /**
         * unlock the panel if locked
         */
        unlockPanel: function(){
            if(this._panelIsLocked){
                this._panelIsLocked = false;
                this.stateButton.addClass('icon-lock-open').removeClass('icon-lock');

                if(_.size(this._unlockCallbackQueue) > 0) {
                    //console.log("Executing queued callbacks in queue: ",this.unlockCallbackQueue);
                    _.each(this._unlockCallbackQueue, function(callback){
                        callback();
                    });
                    //We presume the callbacks have their own calls to render
                    //this.render();
                    this._unlockCallbackQueue = {};
                }

            }
        },

        /**
         * Toggle the lock state of the panel
         */
        toggleLock: function(){
            if(this._panelIsLocked){
                this.unlockPanel();
            } else {
                this.lockPanel();
            }
        },

        /**
         * Blocks the panel
         */
        blockPanel: function(){
            this.$('.panel').addClass('is-loading');
        },

        /**
         * Unblocks the panel
         */
        unblockPanel: function(){
            this.$('.panel').removeClass('is-loading');
        },

        getStorageGroupItem: function(){

           var data = [
                {
                    group:[
                        {type:'idea-list'},
                        {type:'idea-panel'},
                        {type:'message'}
                    ]
                },
                {
                    group:[
                        {type:'idea-list'},
                        {type:'idea-panel'}
                    ]
                }
            ];


           if(window.localStorage.getItem('groups')){
               data = window.localStorage.getItem('groups');
           }

           return data;
        },

        createGroupItem: function(collection){
            var that = this;
            /**
             * PanelGroupItem Creation
             * */
            var Item = Backbone.Model.extend(),
                Items = Backbone.Collection.extend({
                    model: Item
                });

            var groups = new Items(collection.group);

            var GridItem = Marionette.ItemView.extend({
                template: "#tmpl-item-template",
                initialize: function(options){
                    this.views = options.model.toJSON();
                    this.groupManager = options.groupManager;
                },
                onRender: function(){
                    this.$el = this.$el.children();
                    this.$el.unwrap();
                    this.setElement(this.$el);

                    switch(this.views.type){
                        case 'idea-list':
                            console.log('idea-list');
                            var ideaList =  new IdeaList({
                                el: this.$el
                            });
                            this.$el.append(ideaList.render().el);
                            break;
                        case 'idea-panel':
                            console.log('idea-panel');
                            var ideaPanel = new IdeaPanel({
                                el: this.$el
                            });
                            this.$el.append(ideaPanel.render().el);
                            break;
                        case 'message':
                            console.log('message');
                            var messageList = new MessageList({
                                el: this.$el,
                                groupManager: this.groupManager
                            });
                            this.$el.append(messageList.render().el);
                            break;
                        case 'clipboard':
                            console.log('clipboard');
                            var segmentList = new SegmentList({
                                el: this.$el
                            });
                            this.$el.append(segmentList.render().el);
                            break;
                        case 'synthesis':
                            console.log('synthesis');
                            var synthesisPanel = new SynthesisPanel({
                                el: this.$el
                            });
                            this.$el.append(synthesisPanel.render().el);
                            break;
                    }
                }

            });

            var Grid = Marionette.CompositeView.extend({
                template: "#tmpl-grid-template",
                childView: GridItem,
                childViewContainer: ".panelarea-table",
                childViewOptions: {
                   groupManager: that
                },
                events:{
                  'click .add-group':'addGroup',
                  'click .close-group':'closeGroup',
                  'click .lock-group':'lockGroup'
                },
                onRenderTemplate: function(){
                   this.$el.addClass('wrapper-group');
                },
                addGroup: function(){

                    console.log('add group');

                    var Modal = Backbone.Modal.extend({
                        template: _.template($('#tmpl-create-group').html()),
                        cancelEl:'.btn-cancel',
                        initialize: function(){
                            this.$el.addClass('group-modal');
                        },

                        events:{
                           'click .itemGroup article':'addToGroup'
                        },

                        addToGroup: function(e){

                            var type = $(e.target).attr('data-item');

                            console.log('add to group', type)
                        }
                    });

                    var modalView = new Modal();

                    $('.modal').html(modalView.render().el);

                },

                closeGroup: function(){
                    console.log('close button');
                    //TODO: delete reference to localStorage
                    //this.unbind();
                    //this.remove();
                },

                lockGroup: function(e){

                   that.stateButton = $(e.target).children('i');

                   that.toggleLock();
                }

            });

            return new Grid({
                collection: groups
            });
        },

        getGroupItem: function(){
            var items = this.getStorageGroupItem(),
                 that = this;

            items.forEach(function(item){

                var group = that.createGroupItem(item);

                $('#panelarea').append(group.render().el);
            });
        }

    });

    return new groupManager();

});